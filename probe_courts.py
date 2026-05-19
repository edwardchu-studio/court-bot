#!/usr/bin/env python3
"""probe_courts.py — 探测 RIOC Location Requested dropdown 所有可选 court + UUID."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
AUTH = str(ROOT / "auth.json")
URL = "https://rioc.civicpermits.com/Permits/New"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(storage_state=AUTH, viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=20000)
    time.sleep(2)

    # 填 Activity 触发 location 联动加载
    page.get_by_role("textbox", name="Activity", exact=True).fill("Tennis")
    time.sleep(1.5)

    # 拿 Location Requested select 的所有 options
    options = page.locator('select').filter(
        has=page.locator('option', has_text="Tennis")
    ).first.evaluate("""el => Array.from(el.options).map(o => ({
        text: o.textContent.trim(),
        value: o.value,
        disabled: o.disabled,
    }))""")

    print("\n═══ Location Requested 所有 options ═══")
    for o in options:
        if "tennis" in o['text'].lower():
            print(f"  uuid: {o['value']}")
            print(f"  text: {o['text']}")
            print(f"  disabled: {o['disabled']}")
            print()

    # 也试着 select Court 1 然后看 Add Facility modal 里有什么 sub-facility
    if options:
        court1 = next((o for o in options if "tennis" in o['text'].lower() and "1" in o['text']), None)
        if court1:
            print(f"\n试 select Court 1 (uuid={court1['value']}) 看 Add Facility modal 内容...")
            page.get_by_label("Location Requested").select_option(court1['value'])
            time.sleep(1.5)
            add_btn = page.get_by_role("button", name="Add Facility")
            add_btn.click()
            time.sleep(3)
            items = page.get_by_role("listitem").all_text_contents()
            print("\n── Add Facility modal listitems ──")
            for i, txt in enumerate(items[:20]):
                if txt.strip() and len(txt) < 200:
                    print(f"  [{i}] {txt.strip()[:120]}")

    input("\n[按 Enter 关闭浏览器]")
    browser.close()
