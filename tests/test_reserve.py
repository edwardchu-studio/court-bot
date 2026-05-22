"""
test_reserve.py — court-bot 单元测试。

不依赖 Playwright/网络/Discord。覆盖关键的纯函数 + 配置 + CLI parsing。

安装 + 运行:
  .venv/bin/pip install pytest pyyaml
  .venv/bin/pytest tests/ -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ────────────────────────────────────────────────────────────────────────────
# 1. load_config: yaml 读取 + 关键字段必填
# ────────────────────────────────────────────────────────────────────────────


def test_load_config_returns_required_fields():
    """config.yaml 必须包含 location_uuid / preferences / permit_answers / activity_keyword."""
    import reserve

    cfg = reserve.load_config()
    for k in ("location_uuid", "preferences", "permit_answers", "activity_keyword"):
        assert k in cfg, f"config.yaml 缺字段 {k}"


def test_config_preferences_have_start_end_hour():
    """每个 preference 必须有 start_hour + end_hour + label."""
    import reserve

    cfg = reserve.load_config()
    for i, pref in enumerate(cfg["preferences"]):
        assert "start_hour" in pref, f"preferences[{i}] 缺 start_hour"
        assert "label" in pref, f"preferences[{i}] 缺 label"
        # end_hour 可选, fallback to start_hour + 1
        if "end_hour" in pref:
            assert pref["end_hour"] > pref["start_hour"], f"preferences[{i}] end_hour 必须 > start_hour"


def test_config_preference_ordering_makes_sense():
    """preferences 顺序 = 用户优先级. 第一个必须是用户最想抢的时段."""
    import reserve

    cfg = reserve.load_config()
    assert len(cfg["preferences"]) >= 1, "至少 1 个 preference"
    # 检查无重复 start_hour
    starts = [p["start_hour"] for p in cfg["preferences"]]
    assert len(starts) == len(set(starts)), f"preferences 出现重复 start_hour: {starts}"


# ────────────────────────────────────────────────────────────────────────────
# 2. CLI 参数: --location-uuid / --location-label
# ────────────────────────────────────────────────────────────────────────────


def test_main_argparse_accepts_location_uuid():
    """3 并行抢 1/2/3 court 时 launchd 传不同 UUID. CLI 必须接受这个 override."""
    import argparse
    import reserve

    src = (ROOT / "reserve.py").read_text()
    # 必须有 --location-uuid argument 定义
    assert "--location-uuid" in src, "CLI 必须支持 --location-uuid override"
    assert "--location-label" in src, "CLI 必须支持 --location-label (用于 TG push 区分 Court 1/2/3)"


# ────────────────────────────────────────────────────────────────────────────
# 3. fill_form: #event0 click 必须用 explicit timeout (防 RIOC 08:00 30s 死等)
# ────────────────────────────────────────────────────────────────────────────

# CONTEXT (2026-05-16): 实测 RIOC 在 08:00:00 那刻 server overload, #event0 渲染
# 可能延迟 30+ 秒. 之前 page.locator("#event0").click() 用 Playwright default
# 30s timeout 干等, 错过抢券窗口. 必须显式 timeout=10000 让上层 reload-and-retry
# 接管. 这个测试守这条.


def test_event0_click_has_explicit_short_timeout():
    """fill_form 里 click('#event0') 必须显式指定 timeout (不能用 default 30s)."""
    import re

    src = (ROOT / "reserve.py").read_text()
    # 找 #event0 click 调用 (跨多行)
    matches = re.findall(
        r'page\.locator\(["\']#event0["\']\)\.click\([^)]*\)', src, flags=re.DOTALL
    )
    assert matches, "找不到 page.locator('#event0').click(...) 调用"
    for m in matches:
        assert "timeout" in m, (
            f"#event0 click 必须显式 timeout=N (不能用 default 30s): {m}"
        )


# ────────────────────────────────────────────────────────────────────────────
# 4. main loop: 每个 preference 必须有多次 retry (不是 1 次就 give up)
# ────────────────────────────────────────────────────────────────────────────


def test_main_has_per_preference_retry():
    """preference[0] (2-3 PM) 失败时不能立刻切下一个; 必须 reload + 重试同 pref.

    实测 RIOC 08:00:00 那刻 server overload 30s, 重试同 pref 比换 pref 更可能成功."""
    src = (ROOT / "reserve.py").read_text()
    assert "max_retries_per_pref" in src, (
        "main 必须有 max_retries_per_pref 配置 — preference[0] 单次失败立刻 fallback "
        "到 preference[1] 会错过最想要的时段."
    )


# ────────────────────────────────────────────────────────────────────────────
# 5. wait_until: 安全防御 (避免 wait 超长时间或负数)
# ────────────────────────────────────────────────────────────────────────────


def test_wait_until_returns_immediately_for_past_target():
    """如果 target 已过, wait_until 应该立刻返回."""
    import reserve

    past = datetime.now(reserve.NY).replace(microsecond=0) - timedelta(hours=1)
    t0 = datetime.now(reserve.NY)
    reserve.wait_until(past)
    elapsed = (datetime.now(reserve.NY) - t0).total_seconds()
    assert elapsed < 2, f"wait_until(past) 应立即返回, 用了 {elapsed}s"


# ────────────────────────────────────────────────────────────────────────────
# 6. tg_push: token/chat 不存在时不 crash
# ────────────────────────────────────────────────────────────────────────────


def test_tg_push_no_crash_when_missing_env(monkeypatch):
    """没配 TG 也不能 crash bot 主流程."""
    import reserve

    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    # 不应抛异常
    reserve.tg_push("test message")


# ────────────────────────────────────────────────────────────────────────────
# 7. 静态: 禁用 wait_until="networkidle" (RIOC 08:00 server 繁忙不会 idle)
# ────────────────────────────────────────────────────────────────────────────

# CONTEXT (2026-05-14): 之前 page.goto(..., wait_until="networkidle", timeout=20000)
# 在 08:00 RIOC server 繁忙时永远等不到 networkidle → 抢券超时. 必须用
# domcontentloaded.


def test_no_networkidle_wait_until():
    """page.goto / reload 必须用 domcontentloaded 不能用 networkidle."""
    src = (ROOT / "reserve.py").read_text()
    assert 'wait_until="networkidle"' not in src and "wait_until='networkidle'" not in src, (
        "禁用 wait_until='networkidle' — RIOC 在 08:00 抢券峰永远不会 idle, "
        "页面 timeout 错过窗口 (2026-05-14 事故)."
    )


# ────────────────────────────────────────────────────────────────────────────
# 8. FAST PATH (2026-05-21 加): 预热阶段必须走到 #event0 click 打开日历 modal,
#    fill_form 检测 startHour select 在 → 跳过 navigate (节省 ~10s)
# ────────────────────────────────────────────────────────────────────────────

# CONTEXT (2026-05-21): 5-21 早上抢 5-23 周六 3-4 PM 全失败. fill_form 第一次跑
# 要 17s (Activity → Add Facility → #event0 → 日期 → Add & Confirm), 而 RIOC 在
# release 后 ~10s 内秒光黄金时段. FAST PATH 把 navigate (~10s) 移到 release 前
# 完成, release 后 fill_form 检测到日历 modal 已开就跳过 navigate. 这两个测试
# 守这条不退化.


def test_warmup_opens_calendar_modal():
    """main() 预热必须 click #event0 把日历 modal 打开, 否则 FAST PATH 不启用."""
    src = (ROOT / "reserve.py").read_text()
    # 预热段必须含 #event0 click + 等 startHour select 出现
    warmup_section = src[src.find("提前导航 + 预热到日历"):src.find("wait_until(release)")]
    assert warmup_section, "找不到预热代码段"
    assert "#event0" in warmup_section, (
        "预热必须 click '#event0' 把日历 modal 打开 (FAST PATH 关键步骤)"
    )
    assert "startHour" in warmup_section, (
        "预热完成后必须 wait_for_selector startHour, 验证日历 modal 真打开"
    )


def test_fill_form_has_fast_path_detection():
    """fill_form 开头必须检测 startHour select 是否存在, 决定走 fast/slow path."""
    src = (ROOT / "reserve.py").read_text()
    # 找到 fill_form 函数体
    func_start = src.find("def fill_form(page,")
    func_end = src.find("def _fill_permit_questions(", func_start)
    fill_form_src = src[func_start:func_end]
    assert "is_prewarmed" in fill_form_src, (
        "fill_form 必须有 is_prewarmed 检测 (FAST PATH 入口)"
    )
    assert "startHour" in fill_form_src, (
        "is_prewarmed 必须用 startHour select 的存在与否作为信号"
    )


# ────────────────────────────────────────────────────────────────────────────
# 9. retry_interval_ms: 2026-05-21 从 500 降到 200, 加速抢位轮次密度
# ────────────────────────────────────────────────────────────────────────────

# CONTEXT (2026-05-21): 5-21 周六 5-23 抢失败 (3 court × 4 pref × 3 retry = 36
# 次 1 分多钟内全部 'not available'). retry_interval_ms=500 在 RIOC server
# 高峰期太慢 — 500ms 内对手可能已经成功. 降到 200 增加单位时间内尝试次数.
# 测试守这条数值, 避免未来 refactor 不小心改回 500.


def test_retry_interval_ms_is_aggressive():
    """retry_interval_ms 必须 ≤ 300, 高峰期 500 太慢."""
    import reserve
    cfg = reserve.load_config()
    assert "retry_interval_ms" in cfg
    assert cfg["retry_interval_ms"] <= 300, (
        f"retry_interval_ms={cfg['retry_interval_ms']} 太大. "
        f"RIOC 08:00 高峰 500ms 一次 retry 已被对手秒过, 需 ≤ 300."
    )


# ────────────────────────────────────────────────────────────────────────────
# 10. wait_until keep-alive (2026-05-21): FAST PATH 预热把 calendar modal 打开
#     后等到 release 通常 4+ 分钟, modal 可能被 RIOC 前端 idle 关掉. wait_until
#     必须支持可选 keep_alive 回调, 每 30s 触发一次保活.
# ────────────────────────────────────────────────────────────────────────────

# CONTEXT (2026-05-21): 5-21 commit 7586485 加了 FAST PATH 预热到 calendar modal,
# 但手动测试是 --now 触发 (release 即刻), 没经历 5 分钟空闲考验. launchd 模式下
# 07:55 启动 → 07:55:12 modal 打开 → 闲置到 08:00:00 → modal 是否还在? 加 keep_alive
# 回调防御.


def test_wait_until_accepts_keep_alive_callback():
    """wait_until 必须有 keep_alive 可选参数."""
    import inspect
    import reserve
    sig = inspect.signature(reserve.wait_until)
    assert "keep_alive" in sig.parameters, (
        "wait_until 必须有 keep_alive 参数 (FAST PATH modal 保活)"
    )
    assert "keep_alive_interval" in sig.parameters, (
        "wait_until 必须有 keep_alive_interval 参数控制保活频率"
    )


def test_wait_until_triggers_keep_alive_periodically():
    """wait_until 在长等待期间应每 keep_alive_interval 秒触发一次回调."""
    import reserve

    target = datetime.now(reserve.NY) + timedelta(seconds=3.5)
    calls = []
    def _ka():
        calls.append(datetime.now(reserve.NY))
    reserve.wait_until(target, keep_alive=_ka, keep_alive_interval=1.0)

    # 3.5 秒等待, interval=1s → 期望 ~3 次保活 (允许 1-4)
    assert 1 <= len(calls) <= 4, (
        f"keep_alive 调用次数 {len(calls)} 异常 (期望 3.5s/1s ≈ 3 次)"
    )


def test_wait_until_swallows_keep_alive_exception():
    """keep_alive 回调抛异常不能中断 wait_until — 保活是 non-fatal."""
    import reserve

    target = datetime.now(reserve.NY) + timedelta(seconds=1.5)
    def _bad_ka():
        raise RuntimeError("simulated network blip")
    t0 = datetime.now(reserve.NY)
    reserve.wait_until(target, keep_alive=_bad_ka, keep_alive_interval=0.3)
    elapsed = (datetime.now(reserve.NY) - t0).total_seconds()
    assert elapsed >= 1.4, "wait_until 应忽略 keep_alive 异常, 继续等到 target"


def test_main_passes_keep_alive_to_wait_until():
    """main 流程必须给 wait_until 传 page.evaluate 保活."""
    src = (ROOT / "reserve.py").read_text()
    # wait_until(release, ...) 调用必须带 keep_alive
    main_section = src[src.find("提前导航 + 预热到日历"):src.find("到点! 开始 fill_form")]
    assert "wait_until(release" in main_section, "找不到 wait_until(release) 调用"
    assert "keep_alive" in main_section, (
        "main 调 wait_until 必须传 keep_alive 保活 (FAST PATH modal 长闲置防御)"
    )
    assert "page.evaluate" in main_section or "page.mouse" in main_section, (
        "keep_alive 必须做点轻量 Playwright 交互 (page.evaluate 或 mouse.move)"
    )


def test_modal_keepalive_interval_in_config():
    """config.yaml 必须有 modal_keepalive_interval_sec 字段."""
    import reserve
    cfg = reserve.load_config()
    assert "modal_keepalive_interval_sec" in cfg, (
        "config.yaml 缺 modal_keepalive_interval_sec (FAST PATH 保活节奏配置)"
    )
    assert 10 <= cfg["modal_keepalive_interval_sec"] <= 120, (
        f"modal_keepalive_interval_sec={cfg['modal_keepalive_interval_sec']} 不合理. "
        f"太短浪费 CDP, 太长 RIOC SPA 可能已 timeout."
    )
