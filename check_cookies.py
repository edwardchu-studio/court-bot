#!/usr/bin/env python3
"""
check_cookies.py — cookies 健康预检 (每天 20:00 launchd 跑).

背景: 2026-06-09 → 07-07 cookies 失效近一个月, 每天 8:00 抢位全部失败,
失败通知淹没在日常消息里没人注意。此脚本把发现时点从"抢位失败时"提前到
"前一天晚上", 并用高信噪比的独立告警提示人工操作。

检查:
  1. auth.json 存在性 + 年龄 (>25 天 = 即将过期预警)
  2. headless 打开 /Permits/New, 若被 redirect 到登录页 = cookies 已失效

失效 → TG 告警 (含修复命令)。健康 → 静默 (只写日志)。
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
AGE_WARN_DAYS = 25

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
    from playwright.sync_api import sync_playwright
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
        browser.close()

    if "/Login" in final_url or "/Account/Login" in final_url:
        log(f"FAIL: cookies 已失效 (redirect → {final_url}), auth.json 年龄 {age_days:.0f} 天")
        tg_push(
            f"🎾🔴 网球bot cookies 已失效!\n"
            f"auth.json 已 {age_days:.0f} 天 (有效期约 30 天)\n"
            f"明早 8:00 抢位会失败。今晚立即修复 (需手动登录):\n{FIX_CMD}"
        )
        return 1

    if age_days > AGE_WARN_DAYS:
        log(f"WARN: cookies 仍有效但已 {age_days:.0f} 天, 接近 30 天有效期")
        tg_push(
            f"🎾🟡 网球bot cookies 即将过期 ({age_days:.0f}/30 天)\n"
            f"目前仍有效, 建议尽快刷新:\n{FIX_CMD}"
        )
        return 0

    log(f"OK: cookies 有效, auth.json 年龄 {age_days:.0f} 天")
    return 0


if __name__ == "__main__":
    sys.exit(main())
