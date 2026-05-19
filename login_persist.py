#!/usr/bin/env python3
"""
login_persist.py — 一次性脚本: 手动登录 civicpermits, 把 cookies 存到 auth.json.

跑法:
  cd ~/projects/court-bot
  .venv/bin/python login_persist.py

效果:
  - 弹出 Chromium 窗口, 自动打开 civicpermits 登录页
  - 你手动输用户名+密码 (含可能的 CAPTCHA)
  - 登录成功后 (你看到自己已登录的主页), 切回终端按回车
  - auth.json 被生成 → reserve.py 直接复用, 不再需要登录
  - cookies 有效期通常 30 天, 失效后重跑此脚本即可

注意:
  - 不会保存任何密码到磁盘 (只存 session cookies)
  - auth.json 在 .gitignore 里, 防止泄漏
"""
import os
from playwright.sync_api import sync_playwright

URL = "https://rioc.civicpermits.com/Permits/New"
AUTH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.json")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = browser.new_context(viewport=None)  # 用整窗
        page = ctx.new_page()
        print(f"📂 打开 {URL}")
        page.goto(URL)

        print()
        print("═" * 60)
        print("  请在弹出的浏览器里完成登录, 然后导航到任意页面证明已登录")
        print("  (例如点击进入 'New Permit' 表单页能看到预约字段)")
        print("═" * 60)
        input("\n  ↳ 登录完成后, 切回这里按 [Enter] 保存 cookies: ")

        ctx.storage_state(path=AUTH_PATH)
        print(f"\n✅ cookies 已保存 → {AUTH_PATH}")
        print(f"   reserve.py 之后会自动用这个文件免登录")
        browser.close()


if __name__ == "__main__":
    main()
