"""court-bot 管理 UI 后端 (port 7792).

功能: config.yaml 读写(带备份) / launchd 服务开关 / cookies 状态+验证 /
抢位历史解析 / TG 测试 / 手动触发 / SSE 实时执行直播。
"""
import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, date
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
AUTH_PATH = ROOT / "auth.json"
LOGS_DIR = ROOT / "logs"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"

BOTS = {
    "daily":  {"plist": "me.tennis-bot",        "label": "每日抢位 (T+2)",       "court": "Court 1"},
    "court1": {"plist": "me.tennis-bot-court1", "label": "周四/五 · Court 1",    "court": "Court 1"},
    "court2": {"plist": "me.tennis-bot-court2", "label": "周四/五 · Court 2",    "court": "Court 2"},
    "court3": {"plist": "me.tennis-bot-court3", "label": "周四/五 · Court 3",    "court": "Court 3"},
    "cookiecheck": {"plist": "me.tennis-bot-cookiecheck", "label": "每晚 20:00 cookies 预检", "court": "-"},
}

app = FastAPI(title="court-bot 管理台", docs_url="/api/docs")


def _uid() -> int:
    return os.getuid()


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _save_config(cfg: dict) -> None:
    # 备份后原子写入
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(CONFIG_PATH, CONFIG_PATH.with_suffix(f".yaml.bak_{ts}"))
    # 只保留最近 10 个备份
    baks = sorted(ROOT.glob("config.yaml.bak_*"), reverse=True)
    for old in baks[10:]:
        old.unlink()
    tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w") as f:
        f.write("# 网球场预约配置 (由管理台生成 — 手动编辑也 OK, reserve.py 直接读这个文件)\n")
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp.rename(CONFIG_PATH)


# ── 状态总览 ──
@app.get("/api/status")
def status():
    # cookies
    cookies = {"exists": AUTH_PATH.exists()}
    if cookies["exists"]:
        age_days = (time.time() - AUTH_PATH.stat().st_mtime) / 86400
        cookies["age_days"] = round(age_days, 1)
        cookies["health"] = "ok" if age_days < 25 else ("warn" if age_days < 30 else "expired")
        cookies["updated_at"] = datetime.fromtimestamp(AUTH_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    # launchd 服务状态
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    loaded = set(re.findall(r"(me\.tennis-bot[\w.-]*)", out))
    bots = {}
    for bid, b in BOTS.items():
        bots[bid] = {"label": b["label"], "court": b["court"], "plist": b["plist"],
                     "enabled": b["plist"] in loaded}

    # 下一次抢位预告
    cfg = _load_config()
    offset = cfg.get("target_offset_days", 2)
    today = date.today()
    rel = cfg.get("release_time", {})

    return {"cookies": cookies, "bots": bots,
            "next_run": {"time": f"{rel.get('hour', 8):02d}:{rel.get('minute', 0):02d} ET",
                         "target_date": str(today.fromordinal(today.toordinal() + offset)),
                         "offset_days": offset}}


# ── 配置 ──
@app.get("/api/config")
def get_config():
    return _load_config()


class ConfigPatch(BaseModel):
    preferences: list[dict] | None = None
    target_offset_days: int | None = None
    release_time: dict | None = None
    max_attempts: int | None = None
    retry_interval_ms: int | None = None
    attempt_timeout_sec: int | None = None
    permit_answers: dict | None = None
    notify_on_start: bool | None = None
    notify_on_success: bool | None = None
    notify_on_failure: bool | None = None
    screenshot_on_error: bool | None = None


@app.patch("/api/config")
def patch_config(body: ConfigPatch):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    # 校验
    if "preferences" in patch:
        if not patch["preferences"]:
            raise HTTPException(400, "至少要有一个偏好时段")
        for p in patch["preferences"]:
            sh, eh = p.get("start_hour"), p.get("end_hour")
            if not (isinstance(sh, int) and isinstance(eh, int) and 6 <= sh <= 21 and sh < eh <= 22):
                raise HTTPException(400, f"非法时段: {p} (start 6-21, end>start, ≤22)")
            if not p.get("label"):
                p["label"] = f"{sh % 12 or 12}-{eh % 12 or 12} {'PM' if sh >= 12 else 'AM'}"
    if "target_offset_days" in patch and not (0 <= patch["target_offset_days"] <= 14):
        raise HTTPException(400, "target_offset_days 范围 0-14")
    if "max_attempts" in patch and not (1 <= patch["max_attempts"] <= 200):
        raise HTTPException(400, "max_attempts 范围 1-200")
    if "release_time" in patch:
        rt = patch["release_time"]
        if not (0 <= rt.get("hour", -1) <= 23 and 0 <= rt.get("minute", -1) <= 59):
            raise HTTPException(400, "release_time 需要 hour 0-23 / minute 0-59")
        rt.setdefault("second", 0)

    cfg = _load_config()
    cfg.update(patch)
    _save_config(cfg)
    return cfg


# ── launchd 服务开关 ──
class BotToggle(BaseModel):
    enabled: bool


@app.post("/api/bots/{bot_id}/toggle")
def toggle_bot(bot_id: str, body: BotToggle):
    if bot_id not in BOTS:
        raise HTTPException(404, "未知 bot")
    plist_name = BOTS[bot_id]["plist"]
    plist_path = LAUNCH_AGENTS / f"{plist_name}.plist"
    if not plist_path.exists():
        raise HTTPException(500, f"plist 不存在: {plist_path}")
    if body.enabled:
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist_path)],
                           capture_output=True, text=True)
        # 已加载时 bootstrap 返回非 0, 视为成功
        if r.returncode != 0 and "already" not in (r.stderr or "").lower() and "in progress" not in (r.stderr or "").lower():
            raise HTTPException(500, f"启用失败: {r.stderr[:200]}")
    else:
        r = subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{plist_name}"],
                           capture_output=True, text=True)
        if r.returncode != 0 and "no such" not in (r.stderr or "").lower():
            raise HTTPException(500, f"禁用失败: {r.stderr[:200]}")
    return {"ok": True, "enabled": body.enabled}


@app.post("/api/bots/{bot_id}/kickstart")
def kickstart_bot(bot_id: str):
    """立即手动触发一次 (真实抢位, 会真的提交 permit!)."""
    if bot_id not in BOTS:
        raise HTTPException(404, "未知 bot")
    plist_name = BOTS[bot_id]["plist"]
    r = subprocess.run(["launchctl", "kickstart", f"gui/{_uid()}/{plist_name}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(500, f"触发失败: {r.stderr[:200]}")
    return {"ok": True}


# ── cookies ──
@app.post("/api/cookies/check")
def check_cookies_now():
    """实时验证 cookies (headless 访问预约页, ~15s)."""
    r = subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "check_cookies.py")],
                       capture_output=True, text=True, timeout=90, cwd=ROOT)
    out = (r.stdout or "") + (r.stderr or "")
    return {"valid": r.returncode == 0, "detail": out.strip()[-400:]}


# ── 历史记录 ──
@app.get("/api/history")
def history(limit: int = 30):
    """解析 reserve_*.log: 每天的抢位结果."""
    results = []
    for logfile in sorted(LOGS_DIR.glob("reserve_*.log"), reverse=True)[:limit]:
        m = re.match(r"reserve_(\d{8})_", logfile.name)
        if not m:
            continue
        day = m.group(1)
        text = logfile.read_text(errors="ignore")
        if "启动" not in text and len(text) < 300:
            continue  # import 副作用产生的空 log, 跳过
        target = None
        tm = re.search(r"target_date=(\d{4}-\d{2}-\d{2})", text) or \
             re.search(r"目标日: (\d{4}-\d{2}-\d{2})", text)
        if tm:
            target = tm.group(1)
        entry = {"run_date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "target_date": target,
                 "log_file": logfile.name}
        if "✅" in text and "抢到了" in text:
            slot = re.search(r"抢到了!\n日期: [\d-]+\n时段: ([^\n]+)", text)
            entry["result"] = "success"
            entry["detail"] = slot.group(1) if slot else "已抢到"
        elif "cookies 失效" in text:
            entry["result"] = "cookie_expired"
            entry["detail"] = "cookies 失效, 未能登录"
        elif "所有 4 个 preferences 都不可用" in text or "全部时段不可用" in text or "没抢到" in text:
            entry["result"] = "unavailable"
            entry["detail"] = "全部时段不可用/被抢光"
        else:
            entry["result"] = "unknown"
            entry["detail"] = "未识别 (查看日志)"
        # 关联截图
        shots = sorted(LOGS_DIR.glob(f"screen_{day}_*.png"))
        entry["screenshots"] = [s.name for s in shots]
        results.append(entry)
    return results


@app.get("/api/logs/{name}")
def get_log(name: str):
    """查看单个日志/截图 (只允许 logs 目录内文件)."""
    if "/" in name or ".." in name:
        raise HTTPException(400, "非法文件名")
    p = LOGS_DIR / name
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    if name.endswith(".png"):
        return FileResponse(p, media_type="image/png")
    return FileResponse(p, media_type="text/plain")


# ── SSE 实时执行直播 ──
def _reserve_running() -> bool:
    return subprocess.run(["pgrep", "-f", "reserve[.]py"],
                          capture_output=True).returncode == 0


def _latest_reserve_log() -> Path | None:
    logs = sorted(LOGS_DIR.glob("reserve_*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


@app.get("/api/live/stream")
async def live_stream():
    """SSE: 实时推送 reserve.py 执行状态 + 最新日志增量.

    事件:
      status  — {running: bool, log_file: str|null}  每 2s 心跳
      logfile — {name}                                检测到新的执行日志 (新一次抢位开始)
      lines   — {text}                                日志增量内容
    """
    async def gen():
        cur_file: Path | None = None
        offset = 0
        first = True
        try:
            while True:
                latest = _latest_reserve_log()
                running = _reserve_running()

                if latest and latest != cur_file:
                    cur_file = latest
                    size = cur_file.stat().st_size
                    # 首次连接只回放尾部 4KB 上下文; 之后的新文件从头直播
                    offset = max(0, size - 4000) if first else 0
                    yield ("data: " + json.dumps(
                        {"event": "logfile", "name": cur_file.name},
                        ensure_ascii=False) + "\n\n")
                first = False

                if cur_file and cur_file.exists():
                    size = cur_file.stat().st_size
                    if size > offset:
                        with open(cur_file, "r", errors="ignore") as f:
                            f.seek(offset)
                            chunk = f.read(size - offset)
                        offset = size
                        yield ("data: " + json.dumps(
                            {"event": "lines", "text": chunk},
                            ensure_ascii=False) + "\n\n")

                yield ("data: " + json.dumps(
                    {"event": "status", "running": running,
                     "log_file": cur_file.name if cur_file else None},
                    ensure_ascii=False) + "\n\n")
                await asyncio.sleep(2 if not running else 0.8)
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── 通知测试 ──
@app.post("/api/tg/test")
def tg_test():
    # 不 import reserve (其顶层 logging 初始化会创建空的 reserve_*.log 污染历史)
    import requests
    tok, chat = os.getenv("TG_BOT_TOKEN", ""), os.getenv("TG_CHAT_ID", "")
    if not (tok and chat):
        env_file = Path.home() / ".openclaw" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("TG_BOT_TOKEN="):
                    tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("TG_CHAT_ID="):
                    chat = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not (tok and chat):
        raise HTTPException(500, "TG_BOT_TOKEN/TG_CHAT_ID 未配置")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat,
                            "text": f"🎾 管理台测试消息 · {datetime.now().strftime('%m-%d %H:%M:%S')} — 通知链路正常"},
                      timeout=10)
    if r.status_code != 200:
        raise HTTPException(500, f"TG 返回 {r.status_code}")
    return {"ok": True}


# ── UI ──
@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")
