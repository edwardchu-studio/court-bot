#!/usr/bin/env python3
"""
check_cookies.py — cookies 健康预检 (每天 20:00 launchd 跑).

背景: 2026-06-09 → 07-07 cookies 失效近一个月, 每天 8:00 抢位全部失败,
失败通知淹没在日常消息里没人注意。此脚本把发现时点从"抢位失败时"提前到
"前一天晚上", 并用高信噪比的独立告警提示人工操作。

职责 (2026-07-14 升级为"预检 + 续期心跳"):
  1. headless 打开 /Permits/New, 被踢到登录页 = 失效 → TG 告警
  2. session 有效 → 回写 rotate 后的新 session = 续期 48h
     (RIOC session 生命 ~48h; 每 12h 续一次 → 理论永续, 除非连续 4 次失败)
  3. 距上次续期 >1.5 天 → 黄色预警 (续期机制失灵的信号)
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUTH_PATH = ROOT / "auth.json"
LOG_PATH = ROOT / "logs" / "check_cookies.log"
URL = "https://rioc.civicpermits.com/Permits/New"
RENEW_WARN_DAYS = 1.5   # 距上次续期超过此天数 = 续期机制失灵预警

FIX_CMD = "cd ~/projects/court-bot && .venv/bin/python login_persist.py"


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def tg_push(msg: str) -> None:
    import requests
    tok = os.getenv("TG_BOT_TOKEN", "")
    chat = os.getenv("TG_CHAT_ID", "")
    if not (tok and chat):
        env_file = Path.home() / ".openclaw" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("TG_BOT_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TG_CHAT_ID="):
                    chat = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not (tok and chat):
        log("TG 未配置, 跳过通知")
        return
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            data={"chat_id": chat, "text": msg},
            timeout=10,
        )
        log(f"TG push status={resp.status_code}")
    except Exception as e:
        log(f"TG push exception: {e}")


def main() -> int:
    # 1. auth.json 存在 + 年龄
    if not AUTH_PATH.exists():
        log("FAIL: auth.json 不存在")
        tg_push(f"🎾🔴 网球bot: auth.json 不存在!\n明早抢位必失败。立即修复:\n{FIX_CMD}")
        return 1

    age_days = (time.time() - AUTH_PATH.stat().st_mtime) / 86400

    # 2. 实际验证 cookies (headless 访问, 看是否被踢到登录页)
    # 2026-07-14 关键认知: RIOC session 生命 ~48h, "30 天"是错觉 —
    # 之前 bot 每天成功回写 = 无意中每日续期。现在由预检统一负责续期:
    # 每 12h (07:30/20:00) 访问成功后回写 rotate 后的新 session → 永续。
    from playwright.sync_api import sync_playwright
    renewed = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(AUTH_PATH))
        page = ctx.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"WARN: 页面加载异常 (网络/网站问题, 非 cookies 判定): {e}")
            browser.close()
            return 0  # 网站抽风不算 cookies 失效, 避免误报
        final_url = page.url
        if "/Login" not in final_url and "/Account/Login" not in final_url:
            # session 有效 → 回写 rotate 后的 session = 续期 48h
            ctx.storage_state(path=str(AUTH_PATH))
            renewed = True
        browser.close()

    if "/Login" in final_url or "/Account/Login" in final_url:
        log(f"FAIL: cookies 已失效 (redirect → {final_url}), auth.json 年龄 {age_days:.0f} 天")
        tg_push(
            f"🎾🔴 网球bot cookies 已失效!\n"
            f"距上次续期 {age_days:.1f} 天 (RIOC session 生命 ~2 天)\n"
            f"下次抢位会失败。请立即修复 (需手动登录):\n{FIX_CMD}"
        )
        return 1

    # age 现在的含义 = 距上次成功续期的时间。正常应 <12h (每次预检都续)。
    # >1.5 天 = 连续 3 次续期失败 → session 只剩半条命, 提前告警
    if age_days > RENEW_WARN_DAYS and not renewed:
        log(f"WARN: 续期机制连续失败, 距上次续期已 {age_days:.1f} 天 (session ~2 天死)")
        tg_push(
            f"🎾🟡 网球bot cookies 续期连续失败 ({age_days:.1f} 天未续)\n"
            f"session 生命约 2 天, 即将失效。建议主动刷新:\n{FIX_CMD}"
        )
        return 0

    log("OK: cookies 有效" + (" + 已续期 48h" if renewed else " (未续期)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
