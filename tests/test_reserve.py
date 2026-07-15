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


# ── check_cookies.py 预检脚本 (2026-07-07 加, 防 cookies 静默失效一个月) ──

def test_check_cookies_importable():
    """check_cookies.py 可 import 且关键常量正确."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "check_cookies", Path(__file__).parent.parent / "check_cookies.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.RENEW_WARN_DAYS == 1.5
    assert "login_persist.py" in mod.FIX_CMD
    assert "/Permits/New" in mod.URL


def test_cookiecheck_launchd_plist_exists():
    """cookiecheck launchd plist 存在且每天 20:00 跑."""
    from pathlib import Path
    p = Path.home() / "Library/LaunchAgents/me.tennis-bot-cookiecheck.plist"
    assert p.exists(), "cookiecheck plist 被删了 — cookies 失效将再次静默一个月"
    content = p.read_text()
    assert "<integer>20</integer>" in content
    assert "check_cookies.py" in content


# ── webui (2026-07-12): 管理台 API ──

def _client(tmp_path, monkeypatch):
    import shutil, sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from fastapi.testclient import TestClient
    from webui import server
    # 隔离 config: 拷贝真实 config 到 tmp
    tmp_cfg = tmp_path / "config.yaml"
    shutil.copy2(server.CONFIG_PATH, tmp_cfg)
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_cfg)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    return TestClient(server.app), server, tmp_cfg


def test_webui_config_roundtrip(tmp_path, monkeypatch):
    """UI 保存后的 config.yaml 必须保留 reserve.py 需要的全部字段."""
    client, server, tmp_cfg = _client(tmp_path, monkeypatch)
    r = client.patch("/api/config", json={
        "preferences": [{"start_hour": 15, "end_hour": 16, "label": "3-4 PM"}],
        "target_offset_days": 3,
    })
    assert r.status_code == 200
    import yaml
    cfg = yaml.safe_load(tmp_cfg.read_text())
    # reserve.py 依赖的关键字段一个都不能丢
    for key in ("url", "activity_keyword", "location_uuid", "facility_listitem_text",
                "permit_answers", "release_time", "max_attempts", "preferences",
                "target_offset_days"):
        assert key in cfg, f"字段 {key} 在 UI 写回后丢失 — reserve.py 会崩"
    assert cfg["target_offset_days"] == 3
    assert cfg["preferences"][0]["start_hour"] == 15


def test_webui_config_validation(tmp_path, monkeypatch):
    client, *_ = _client(tmp_path, monkeypatch)
    assert client.patch("/api/config", json={"preferences": []}).status_code == 400
    assert client.patch("/api/config", json={
        "preferences": [{"start_hour": 23, "end_hour": 24}]}).status_code == 400
    assert client.patch("/api/config", json={"target_offset_days": 99}).status_code == 400
    assert client.patch("/api/config", json={"max_attempts": 0}).status_code == 400


def test_webui_config_backup_created(tmp_path, monkeypatch):
    client, server, tmp_cfg = _client(tmp_path, monkeypatch)
    client.patch("/api/config", json={"max_attempts": 50})
    baks = list(tmp_path.glob("config.yaml.bak_*"))
    assert len(baks) == 1, "每次保存必须先备份"


def test_webui_log_path_traversal_blocked(tmp_path, monkeypatch):
    client, *_ = _client(tmp_path, monkeypatch)
    assert client.get("/api/logs/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert client.get("/api/logs/../auth.json").status_code in (400, 404)


def test_webui_status_shape(tmp_path, monkeypatch):
    client, *_ = _client(tmp_path, monkeypatch)
    s = client.get("/api/status").json()
    assert "cookies" in s and "bots" in s and "next_run" in s
    assert set(s["bots"].keys()) == {"daily", "court1", "court2", "court3", "cookiecheck"}


def test_no_auth_writeback_in_reserve():
    """2026-07-12 事故: reserve.py 回写 auth.json 会把 30 天 persistent cookie
    换成 ~48h session cookie → 2 天后全线失效. 严禁恢复回写."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "reserve.py").read_text()
    import re
    active_writeback = re.findall(r"^\s*ctx\.storage_state\(path=AUTH_PATH\)", src, re.M)
    assert not active_writeback, "reserve.py 恢复了 auth.json 回写 — 会导致 cookies 2 天失效"


def test_cookiecheck_runs_morning_and_evening():
    """预检必须早晚双跑: 20:00 (给当晚修复时间) + 07:30 (抢位前最后一道岗)."""
    from pathlib import Path
    p = Path.home() / "Library/LaunchAgents/me.tennis-bot-cookiecheck.plist"
    content = p.read_text()
    assert "<integer>20</integer>" in content
    assert "<integer>7</integer>" in content and "<integer>30</integer>" in content


def test_check_cookies_renews_session():
    """2026-07-14: RIOC session 生命 ~48h, 预检必须承担续期职责 (成功访问后回写)."""
    from pathlib import Path
    src = (Path(__file__).parent.parent / "check_cookies.py").read_text()
    assert "ctx.storage_state(path=str(AUTH_PATH))" in src, "预检丢失续期回写 — session 将每 48h 死一次"
    assert "renewed = True" in src
