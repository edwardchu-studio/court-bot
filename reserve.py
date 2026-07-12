#!/usr/bin/env python3
"""
reserve.py — 主抢位脚本. 自动用 auth.json 登录, 填表, 等到 08:00:00.000 提交.

时间精度:
  - 提前 5 分钟启动 (launchd 07:55 ET)
  - 07:55-07:59 完成导航 + 填表 + 等待提交按钮可点击
  - 08:00:00.000 (毫秒级精确) 点击 submit
  - 失败则按 config.yaml 的 max_attempts/retry_interval 重试

模板 selector 占位符 [TODO_SELECTOR]:
  请用 `playwright codegen` 录制一次手动预约流程, 把真实的 selector 填进来.
  详见 README.md 的 "selector 探测" 章节.
"""
import os
import re
import sys
import time
import json
import logging
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import yaml
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

NY = ZoneInfo("America/New_York")
AUTH_PATH = str(ROOT / "auth.json")
CONFIG_PATH = ROOT / "config.yaml"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 日志: 文件 + console
log_file = LOG_DIR / f"reserve_{datetime.now(NY).strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
log = logging.getLogger("reserve")

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════

def tg_push(msg: str) -> None:
    """推 Telegram. 失败不抛, 但 log 完整 status / response body 用于诊断."""
    log.info("TG push 准备发送 (token=%s chat=%s)",
             (TG_TOKEN[:12] + "...") if TG_TOKEN else "❌空", TG_CHAT or "❌空")
    if not (TG_TOKEN and TG_CHAT):
        log.warning("TG 未配置, 跳过: %s", msg[:60])
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        log.info("TG push status=%d body=%s", resp.status_code, resp.text[:120])
    except Exception as e:
        log.warning("TG push exception: %s", e)


def wait_until(target: datetime) -> None:
    """忙等到 target_time, 毫秒级精度."""
    while True:
        diff = (target - datetime.now(NY)).total_seconds()
        if diff <= 0:
            return
        if diff > 2:
            time.sleep(diff - 1)   # 远距离粗等
        elif diff > 0.05:
            time.sleep(0.01)       # 近距离精细忙等
        else:
            pass  # 最后 50ms 纯 spin


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def screenshot(page, tag: str) -> str:
    path = LOG_DIR / f"screen_{datetime.now(NY).strftime('%Y%m%d_%H%M%S')}_{tag}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════
# 表单填写 — selector 路径来自 user codegen 录制 (2026-05-13)
# ═══════════════════════════════════════════════════════

def fill_form(page, target_date_iso: str, start_hour: int, end_hour: int,
              activity_keyword: str, location_uuid: str, facility_text: str,
              permit_answers: dict) -> None:
    """填好预约表, 停在 #acceptTerms 已勾选状态. 不点最终 submit.

    流程 (codegen 实测):
      1. 跳 /Permits/New (auth.json 已登录则自动放行)
      2. Activity = "Tennis"
      3. Location Requested select = UUID
      4. 点 Add Facility 按钮
      5. 在弹出 listitem 里选 "Tennis Courts"
      6. 点 #event0 打开日期/时间选择器
      7. 点日期 N (target_date.day)
      8. select startHour
      9. 点 Add & Confirm
      10. 勾 #acceptTerms

    停在 #acceptTerms 后, submit_form() 负责最后一步.
    """
    from datetime import date
    target = date.fromisoformat(target_date_iso)
    log.info("fill_form: date=%s (day=%d) start_hour=%d activity=%s",
             target_date_iso, target.day, start_hour, activity_keyword)

    # 1. 导航到 New Permit — 仅在 URL 不对时 goto, 用 domcontentloaded (不等 networkidle,
    # 抢位高峰 server 不会 idle, networkidle 永远等不到)
    if "/Permits/New" not in page.url:
        try:
            page.goto("https://rioc.civicpermits.com/Permits/New",
                      wait_until="domcontentloaded", timeout=15000)
        except PlaywrightTimeoutError:
            log.warning("goto timeout 15s, 但页面可能已部分加载, 继续")
    if "/Account/Login" in page.url or "/Login" in page.url:
        raise RuntimeError("cookies 失效, 跳到登录页. 请重跑 login_persist.py")

    # 2. Activity (填 Tennis 触发自动联想/搜索)
    page.get_by_role("textbox", name="Activity", exact=True).click()
    page.get_by_role("textbox", name="Activity", exact=True).fill(activity_keyword)

    # 3. Location Requested 下拉选 UUID
    page.get_by_label("Location Requested").select_option(location_uuid)

    # 4. Add Facility — Modal 偶尔不弹, 用 3 次 click retry + 加长等待
    page.wait_for_load_state("domcontentloaded", timeout=10000)
    time.sleep(1.5)  # 给 JS handler 异步绑定时间

    add_btn = page.get_by_role("button", name="Add Facility")
    add_btn.wait_for(state="visible", timeout=10000)

    # 5. retry click Add Facility — 3 次直接 click; 仍失败则 reload page 重做前置步骤再试
    log.info("找 facility listitem '%s'...", facility_text)
    facility_locator = page.get_by_role("listitem").filter(
        has_text=re.compile(facility_text, re.I)
    )

    def _try_click_add_facility(max_clicks=4, wait_per_click_sec=1.5):
        """快速 retry: 每次 click 后只等 1.5s (而非 4s), 4 次 click 共 6s + reload."""
        for click_try in range(max_clicks):
            try:
                add_btn.scroll_into_view_if_needed()
                add_btn.hover()
                time.sleep(0.15)
                add_btn.click(force=True)
            except Exception as e:
                log.warning("Add Facility click %d 失败: %s", click_try + 1, str(e)[:80])
            # 快速轮询: 每 250ms 检查一次, 共 wait_per_click_sec
            elapsed = 0
            while elapsed < wait_per_click_sec:
                if facility_locator.count() > 0:
                    return True
                time.sleep(0.25)
                elapsed += 0.25
        return False

    if not _try_click_add_facility():
        # 第一轮 4 次 click (6s) 失败 → reload 页面 + 重做 Activity/Location, 再试 1 轮
        log.warning("Add Facility 4 次未弹 modal — reload + 重试")
        try:
            page.reload(wait_until="domcontentloaded", timeout=10000)
        except PlaywrightTimeoutError:
            log.warning("reload timeout, 继续")
        time.sleep(1.5)
        page.get_by_role("textbox", name="Activity", exact=True).fill(activity_keyword)
        page.get_by_label("Location Requested").select_option(location_uuid)
        time.sleep(1.5)
        add_btn = page.get_by_role("button", name="Add Facility")
        add_btn.wait_for(state="visible", timeout=8000)
        if not _try_click_add_facility():
            screenshot(page, "add_facility_unrecoverable")
            raise RuntimeError("Add Facility 即使 reload 后 8 次 click 都没弹 modal")
    cnt = facility_locator.count()
    log.info("Add Facility 成功, listitem cnt=%d", cnt)

    log.info("找到 %d 个匹配 '%s' 的 listitem", cnt, facility_text)
    if cnt == 0:
        # debug: dump 所有 listitem 文本帮定位实际可选项
        all_items = page.get_by_role("listitem").all()
        texts = [item.text_content()[:100].strip() for item in all_items]
        log.error("listitem '%s' 未找到. 当前共 %d 个 listitem, 文本(前30): %s",
                  facility_text, len(all_items), texts[:30])
        screenshot(page, "no_facility_listitem")
        raise RuntimeError(
            f"找不到 facility listitem '{facility_text}'. "
            f"实际有 {len(all_items)} 个 listitem. 看 log + 截图核对正确文本"
        )
    facility_locator.first.click()

    # 6. 点 #event0 (第一个 event 卡片) 打开日期/时间选择器
    # RIOC 在 08:00:00 那刻 server overload, #event0 渲染可能延迟 30+ 秒
    # timeout 缩到 10s, 让上层 reload-and-retry-same-pref 接管 (实测 ~33s 后 #event0 即刻渲染)
    page.locator("#event0").click(timeout=10000)

    # 7. 选目标日 — 关键: RIOC 在 release_time 前会把目标日标灰 disabled.
    # 如果 link 不存在 / 不可点, 等到 release_time + 1s (RIOC 服务器开放) 再点.
    # 也加 8 秒短轮询 (覆盖 server clock 漂移).
    target_link = page.get_by_role("link", name=str(target.day), exact=True)
    clicked = False
    for retry in range(16):  # 16 × 0.5s = 8s
        try:
            if target_link.count() > 0:
                target_link.first.click(timeout=1500)
                clicked = True
                break
        except Exception as e:
            log.info("日期 %d 暂不可点 (尝试 %d): %s", target.day, retry + 1, str(e)[:80])
        time.sleep(0.5)
    if not clicked:
        screenshot(page, "date_not_clickable")
        raise RuntimeError(
            f"日期 {target.day} 8s 内不可点击 — 可能 RIOC 还没开放此日窗口, "
            f"或者日历显示的不是 {target.year}-{target.month:02d}"
        )
    log.info("日期 %d 已 click", target.day)

    # 8. 选 start hour (24h, 例如 9 或 14)
    page.locator('select[name="startHour"]').select_option(str(start_hour))

    # 8b. 选 end hour (默认 start + 1; RIOC 限单次 1-2 小时)
    try:
        page.locator('select[name="endHour"]').select_option(str(end_hour))
        log.info("end_hour 已选 %d", end_hour)
    except Exception as e:
        log.warning("end_hour select 失败 (可能字段名不同): %s", e)

    # 9. 关闭可能的下拉 + Add & Confirm
    page.locator("body").click()
    page.get_by_role("button", name="Add & Confirm").click()
    time.sleep(1)  # 等表单 reflow

    # 9c. 检测 RIOC "not available" 红字告警 — 出现就立即放弃这个 slot
    body_text = page.locator("body").inner_text(timeout=2000).lower()
    unavailable_markers = [
        "not available for the above date",
        "facilities are not available",
        "no longer available",
        "already booked",
        "fully booked",
    ]
    hit = next((m for m in unavailable_markers if m in body_text), None)
    if hit:
        screenshot(page, "slot_unavailable")
        raise RuntimeError(f"RIOC 告警: '{hit}' — 此时段不可用 ({target_date_iso} {start_hour}-{end_hour})")

    # 9b. sanity check: 检查页面 HTML (不是 visible text), 看日期是否真存在 hidden input/form data
    try:
        html = page.content()
        date_patterns = [
            target.strftime("%m/%d/%Y"),       # 05/15/2026
            target.strftime("%-m/%-d/%Y"),     # 5/15/2026
            target.strftime("%Y-%m-%d"),       # 2026-05-15
            f"value=\"{target.isoformat()}\"",
            target.strftime("%B %-d"),         # May 15
        ]
        date_hits = [pat for pat in date_patterns if pat in html]
        log.info("sanity: HTML 含日期 %s; 含 facility 数据: %s",
                 date_hits or "❌ 未找到!", "Tennis Courts" in html)
    except Exception as e:
        log.warning("sanity check failed (non-fatal): %s", e)

    # 10. 填 Permit Questions (RIOC 必填; 不填 Submit 灰色 disabled)
    log.info("填 Permit Questions: %d 个答案", len(permit_answers))
    _fill_permit_questions(page, permit_answers)

    # 11. 勾 accept terms
    page.wait_for_selector("#acceptTerms", timeout=5000)
    page.locator("#acceptTerms").check()
    time.sleep(0.5)

    # 12. 等 Submit 按钮变 enabled (Permit Questions 填全 + acceptTerms 勾上之后)
    log.info("等 Submit 按钮 enabled...")
    for retry in range(20):  # 10s 内等
        submit_btn = page.get_by_role("button", name="Submit")
        if submit_btn.count() > 0 and submit_btn.first.is_enabled():
            log.info("Submit 按钮已 enabled ✓")
            break
        time.sleep(0.5)
    else:
        log.warning("Submit 按钮等 10s 仍未 enabled — 可能还有必填字段没填")
        screenshot(page, "submit_not_enabled")

    log.info("fill_form: 完成, 已停在待 submit 状态")


def _fill_permit_questions(page, answers: dict) -> None:
    """填 Permit Questions 区域 9 个必填字段.

    关键词必须够长 + 在页面唯一 (避免误匹配顶部 Activity input label).
    类型自适应: textarea / input / select.
    每字段 short timeout, 失败立刻下一个, 不卡住.
    """
    # 关键词用 Permit Questions 区独有的长片段
    field_map = [
        ("be taking place",      answers.get("activity_description", "")),
        ("how many people",      answers.get("num_people", "")),
        ("participants charged", answers.get("participants_charged", "")),
        ("spectators charged",   answers.get("spectators_charged", "")),
        ("had a permit",         answers.get("prior_permit", "")),
        ("amplified sound",      answers.get("amplified_sound", "")),
        ("be advertised",        answers.get("advertised", "")),
        ("on site security",     answers.get("on_site_security", "")),
        ("parking needs",        answers.get("parking_needs", "")),
    ]
    filled = 0
    for keyword, value in field_map:
        if not value:
            continue
        try:
            # 大小写不敏感匹配 label 含 keyword
            label_locator = page.locator(
                f"xpath=//label[contains("
                f"translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                f"'{keyword.lower()}')]"
            )
            n = label_locator.count()
            if n == 0:
                log.warning("  ✗ label '%s' 没找到", keyword)
                continue
            label = label_locator.first

            # 找 label 之后**第一个**任意表单元素 (textarea/input/select)
            target = label.locator(
                "xpath=following::*[self::textarea or self::input or self::select][1]"
            )
            if target.count() == 0:
                log.warning("  ✗ '%s' 后面没找到表单元素", keyword)
                continue

            tag = target.first.evaluate("el => el.tagName.toLowerCase()")

            try:
                if tag == "select":
                    # select: 用 label 文本; 找不到 fallback 用 value
                    try:
                        target.first.select_option(label=value, timeout=3000)
                    except Exception:
                        target.first.select_option(value, timeout=3000)
                else:
                    # textarea / input
                    target.first.fill(value, timeout=3000)
                filled += 1
                log.info("  ✓ '%s' → <%s> = '%s'", keyword, tag, value[:30])
            except Exception as e:
                log.warning("  ✗ '%s' (<%s>) fill 失败: %s", keyword, tag, str(e)[:100])
                continue

        except Exception as e:
            log.warning("  ✗ '%s' 整体失败: %s", keyword, str(e)[:100])
            continue

    log.info("Permit Questions 填了 %d / %d 个字段", filled, sum(1 for _, v in field_map if v))


def submit_form(page) -> bool:
    """到点 click 最终 Submit 按钮 (user 确认 selector: get_by_role button name='Submit').

    Returns: True 表示疑似成功 (URL/页面文字含 confirmation/success 等关键词)
    """
    submit_btn = page.get_by_role("button", name="Submit")
    if submit_btn.count() == 0:
        log.error("submit_form: 找不到 button[name='Submit']; 看截图核对")
        return False

    # 关键: 检查按钮是否 enabled (disabled 时 click 不会触发但也不报错!)
    if not submit_btn.first.is_enabled():
        log.error("submit_form: Submit 按钮是 DISABLED 状态! Permit Questions 可能没填全")
        screenshot(page, "submit_disabled")
        return False

    log.info("submit_form: clicking Submit (enabled)")
    url_before = page.url
    submit_btn.first.click()

    # 严格判断: 必须 URL 跳转出 /Permits/New 才算成功
    # (在 /Permits/New 上有大量 "successfully" 文案 body 命中是假阳性)
    try:
        page.wait_for_url(lambda u: "/Permits/New" not in u, timeout=8000)
        log.info("submit_form: ✅ URL 跳走了 %s → %s", url_before, page.url)
        return True
    except PlaywrightTimeoutError:
        log.warning("submit_form: URL 未跳转, 仍在 %s — 提交失败", page.url)

    # 没跳转 = 提交失败. 但确认一下页面是否有错误信息
    body = page.locator("body").inner_text(timeout=2000).lower()
    err_markers = [
        "not available", "no longer available", "already booked",
        "please correct", "required field", "error",
    ]
    found_err = [m for m in err_markers if m in body]
    if found_err:
        log.warning("submit_form: 失败 - 页面错误标记: %s", found_err)
    return False


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="手动指定目标日 (YYYY-MM-DD), 用于 dry-run 测试. 不传 = 今天 + target_offset_days")
    parser.add_argument("--no-submit", action="store_true", help="dry-run: 填完表不真的 click Submit")
    parser.add_argument("--now", action="store_true", help="跳过等待 release_time, 立即开抢 (今天 8AM 已过自动启用)")
    parser.add_argument("--location-uuid", help="覆盖 config.location_uuid (Court 1/2/3/... 不同 UUID, 用于并行抢)")
    parser.add_argument("--location-label", default="", help="日志/Telegram 显示的 court 名 (e.g. 'Court 2')")
    args = parser.parse_args()

    cfg = load_config()
    # CLI 覆盖 location_uuid (并行抢多 court 时各 launchd instance 传不同 UUID)
    if args.location_uuid:
        cfg["location_uuid"] = args.location_uuid
        cfg["location_label"] = args.location_label or "Court ?"
    else:
        cfg["location_label"] = "Court 1"   # default 跟现有 config UUID 对应
    now = datetime.now(NY)

    # 目标日期: --date 覆盖优先, 否则 today + offset
    if args.date:
        from datetime import date as _date
        target_date = _date.fromisoformat(args.date)
        log.info("使用 --date 覆盖目标日: %s", target_date)
    else:
        target_date = (now + timedelta(days=cfg["target_offset_days"])).date()

    # 释放时间 = 今天的 08:00:00 ET
    rt = cfg["release_time"]
    release = now.replace(hour=rt["hour"], minute=rt["minute"], second=rt["second"], microsecond=0)
    if now > release or args.now:
        log.warning("release time %s 已过 (或 --now), 立即尝试 (no wait)", release)

    log.info("启动 [%s]: now=%s target_date=%s release=%s",
             cfg.get("location_label", "?"), now, target_date, release)
    if cfg.get("notify_on_start"):
        tg_push(f"🎾 *court-bot 启动 [{cfg.get('location_label','?')}]*\n"
                f"目标日: {target_date}\n释放: {release.strftime('%H:%M:%S ET')}\n"
                f"偏好: {len(cfg['preferences'])} 个时段")

    if not Path(AUTH_PATH).exists():
        tg_push("❌ auth.json 缺失! 请先跑 login_persist.py")
        log.error("auth.json 不存在: %s", AUTH_PATH)
        return 1

    with sync_playwright() as p:
        # headless=False: 真实 Chrome window. RIOC 的 Add Facility 按钮在 headless 下
        # 不响应 click (JS 检测或 handler 异步绑定问题). 实测必须用 headed.
        # launchd 在 GUI session (Mac 不 sleep) 下能弹窗运行.
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            storage_state=AUTH_PATH,
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()

        try:
            pref0 = cfg["preferences"][0]

            # 关键: RIOC 在 release_time 前会标灰目标日, 所以 fill_form 必须在
            # release_time 之后跑 (否则点不到日期). 提前导航 + 等到点再填.
            log.info("提前导航 + 等到释放时间 %s...", release)
            try:
                # 提前导航用 domcontentloaded (不依赖 networkidle, 服务器繁忙时永远不 idle)
                page.goto("https://rioc.civicpermits.com/Permits/New",
                          wait_until="domcontentloaded", timeout=15000)
                # 关键: 预先填 Activity + Location, 让 Add Facility 按钮 JS handler 提前注册
                # 这样 08:00:00 click Add Facility 时不会因为 server 繁忙 JS 没绑定而失效
                page.get_by_role("textbox", name="Activity", exact=True).fill(cfg["activity_keyword"])
                page.get_by_label("Location Requested").select_option(cfg["location_uuid"])
                time.sleep(2)
                # 预热 click Add Facility, 看 modal 弹出后 Cancel 它 (UI 重置, JS handler 已 warmed)
                try:
                    warmup_btn = page.get_by_role("button", name="Add Facility")
                    if warmup_btn.count() > 0:
                        warmup_btn.click(force=True)
                        time.sleep(2)
                        # Cancel 这次 dummy Add Facility (按钮通常是 "Cancel")
                        cancel = page.get_by_role("button", name=re.compile(r"^cancel$", re.I))
                        if cancel.count() > 0:
                            cancel.first.click()
                            log.info("✓ Add Facility 预热完成 (modal warm 后 cancel)")
                            time.sleep(1)
                        else:
                            # 没 Cancel, reload 重置
                            page.reload(wait_until="domcontentloaded", timeout=10000)
                            time.sleep(1.5)
                            page.get_by_role("textbox", name="Activity", exact=True).fill(cfg["activity_keyword"])
                            page.get_by_label("Location Requested").select_option(cfg["location_uuid"])
                            log.info("✓ Add Facility 预热 + reload 重置完成")
                except Exception as e:
                    log.warning("预热失败 (non-fatal): %s", e)
            except PlaywrightTimeoutError:
                log.warning("提前导航 timeout, 继续等到点再尝试")
            wait_until(release)
            log.info("⏰ 到点! 开始 fill_form + submit")

            # 尝试第一个偏好 — 每个 preference 给 N 次 retry (default 3) 再切下一个
            # 因为 08:00:00 那刻 RIOC server 可能短暂 overload (#event0 渲染卡 30s),
            # reload 后再试同一个 preference 可能就 OK — 实测 33s 后 server 恢复
            initial_filled = False
            max_retries_per_pref = cfg.get("max_retries_per_pref", 3)
            for try_idx, pref0 in enumerate(cfg["preferences"]):
                pref_done = False
                for retry in range(max_retries_per_pref):
                    try:
                        fill_form(
                            page,
                            target_date.isoformat(),
                            pref0["start_hour"],
                            pref0.get("end_hour", pref0["start_hour"] + 1),
                            cfg["activity_keyword"],
                            cfg["location_uuid"],
                            cfg["facility_listitem_text"],
                            cfg.get("permit_answers", {}),
                        )
                        log.info("✓ 已预填 preference[%d]: %s (第 %d 次)", try_idx, pref0, retry + 1)
                        initial_filled = True
                        pref_done = True
                        break
                    except Exception as e:
                        log.warning("preference[%d] %s 第 %d/%d 次失败: %s",
                                    try_idx, pref0, retry + 1, max_retries_per_pref, str(e)[:120])
                        # reload 重置后下一次 retry (无论是同 pref 还是下一个 pref)
                        try:
                            page.goto("https://rioc.civicpermits.com/Permits/New",
                                      wait_until="domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                if pref_done:
                    break
                log.info("preference[%d] %d 次 retry 用尽, 切下一个", try_idx, max_retries_per_pref)
            if not initial_filled:
                log.error("所有 %d 个 preferences 都不可用", len(cfg["preferences"]))
                screenshot(page, "all_prefs_unavailable")
                tg_push(f"❌ *court-bot 全部时段不可用*\n目标日 {target_date}\n"
                        f"试了 {len(cfg['preferences'])} 个时段全部失败 (可能已全部被抢光)")
                return 2

            # 3. 重试循环 — 第一个偏好失败试下一个
            t0 = time.time()
            success = False
            for attempt in range(cfg["max_attempts"]):
                if time.time() - t0 > cfg["attempt_timeout_sec"]:
                    log.warning("总超时 %ds 达到, 放弃", cfg["attempt_timeout_sec"])
                    break

                # 关键: 用 "最后成功填的 preference" 而非 attempt 索引 — 因为 fill_form 失败时
                # main 自己 iterate preferences, attempt loop 不一定与 preferences[idx] 对应
                # 这里用 main 顶部 "initial_filled" 时的 try_idx 已保存在 pref0 — 后续切换重填也更新 pref0
                pref = pref0   # last successfully filled preference

                try:
                    if args.no_submit:
                        log.info("DRY RUN: --no-submit 跳过真实 Submit click")
                        screenshot(page, "dry_run_ready_to_submit")
                        tg_push(f"🔍 *dry-run 完成*\n目标日: {target_date}\n停在待 Submit 页面.\n截图: logs/")
                        success = True
                        break
                    if submit_form(page):
                        success = True
                        success_path = screenshot(page, "success")
                        msg = (f"✅ *抢到了!*\n日期: {target_date}\n"
                               f"时段: {pref['label']} (startHour={pref['start_hour']})\n"
                               f"用时: {time.time()-t0:.2f}s\n尝试: 第 {attempt+1} 次")
                        tg_push(msg)
                        log.info(msg)
                        break
                    log.info("第 %d 次未成功, 0.5s 后重试", attempt+1)
                except PlaywrightTimeoutError as e:
                    log.warning("attempt %d timeout: %s", attempt+1, e)
                except Exception as e:
                    log.warning("attempt %d error: %s", attempt+1, e)

                time.sleep(cfg["retry_interval_ms"] / 1000.0)

                # 切换偏好: 重新填表 (下一个 start_hour)
                pref = cfg["preferences"][(attempt + 1) % len(cfg["preferences"])]
                try:
                    fill_form(
                        page,
                        target_date.isoformat(),
                        pref["start_hour"],
                        pref.get("end_hour", pref["start_hour"] + 1),
                        cfg["activity_keyword"],
                        cfg["location_uuid"],
                        cfg["facility_listitem_text"],
                        cfg.get("permit_answers", {}),
                    )
                except Exception as e:
                    log.warning("切换偏好 %s 失败: %s", pref, e)

            if not success:
                fail_path = screenshot(page, "fail_final")
                msg = (f"❌ *没抢到*\n日期: {target_date}\n尝试 {attempt+1} 次, 总用时 {time.time()-t0:.1f}s\n"
                       f"截图: `{fail_path}`")
                tg_push(msg)
                log.error(msg)

            # 4. 【2026-07-12 事故修复】禁止回写 auth.json!
            # 原逻辑 ctx.storage_state(path=AUTH_PATH) 会把 login_persist 的 30 天
            # persistent cookie 替换成服务器 rotate 后的 ~48h 短命 session cookie:
            # 7/10 08:02 回写 → 7/12 08:00 失效 (5/22→6/9 同一模式)。
            # login_persist.py 的原始 cookie 保持不动才能活满 30 天。

            browser.close()
            return 0 if success else 1

        except Exception as e:
            log.error("致命错误: %s\n%s", e, traceback.format_exc())
            shot = screenshot(page, "fatal") if 'page' in dir() else ""
            tg_push(f"💥 *court-bot 崩溃*\n{e}\nlog: `{log_file}`\n截图: `{shot}`")
            browser.close()
            return 3


if __name__ == "__main__":
    sys.exit(main())
