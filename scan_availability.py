#!/usr/bin/env python3
"""scan_availability.py — 扫描指定日期所有时段可用性 (不真预订).

用法:
  cd ~/projects/court-bot
  .venv/bin/python scan_availability.py 2026-05-16

输出:
  哪些 hour 还 available, 哪些 not available.
"""
import sys
import time
import yaml
import logging
from datetime import date
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scan")

ROOT = Path(__file__).parent
AUTH = str(ROOT / "auth.json")
URL = "https://rioc.civicpermits.com/Permits/New"


def scan(target_date_str: str, hours_to_scan: list[int]) -> dict:
    with open(ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    target = date.fromisoformat(target_date_str)
    results = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(storage_state=AUTH, viewport={"width": 1400, "height": 900})
        page = ctx.new_page()

        for hour in hours_to_scan:
            try:
                log.info("── 测 %s %02d:00-%02d:00 ──", target_date_str, hour, hour + 1)
                page.goto(URL, wait_until="domcontentloaded", timeout=15000)
                time.sleep(1.5)

                page.get_by_role("textbox", name="Activity", exact=True).fill(cfg["activity_keyword"])
                page.get_by_label("Location Requested").select_option(cfg["location_uuid"])
                time.sleep(1)

                # Add Facility (3 次 retry)
                add_btn = page.get_by_role("button", name="Add Facility")
                add_btn.wait_for(state="visible", timeout=10000)
                fac = page.get_by_role("listitem").filter(has_text=cfg["facility_listitem_text"])
                for click_try in range(3):
                    add_btn.click(force=True)
                    for _ in range(8):
                        if fac.count() > 0:
                            break
                        time.sleep(0.5)
                    if fac.count() > 0:
                        break
                if fac.count() == 0:
                    results[hour] = "ERR_NO_LISTITEM"
                    continue
                fac.first.click()

                page.locator("#event0").click()
                page.get_by_role("link", name=str(target.day), exact=True).click()
                page.locator('select[name="startHour"]').select_option(str(hour))
                page.locator('select[name="endHour"]').select_option(str(hour + 1))
                page.locator("body").click()
                page.get_by_role("button", name="Add & Confirm").click()
                time.sleep(1.5)

                body = page.locator("body").inner_text(timeout=2000).lower()
                if "not available" in body or "no longer available" in body:
                    results[hour] = "❌ NOT_AVAILABLE"
                else:
                    results[hour] = "✅ AVAILABLE"
                    # 截图保留可用证据
                    page.screenshot(path=str(ROOT / "logs" / f"available_{target_date_str}_{hour:02d}.png"))

            except Exception as e:
                results[hour] = f"ERR: {str(e)[:60]}"
                continue

        browser.close()

    return results


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-05-16"
    hours = list(range(8, 20))  # 8 AM ~ 7 PM
    log.info("扫描 %s 共 %d 个时段...", date_str, len(hours))
    results = scan(date_str, hours)

    print()
    print("═" * 50)
    print(f"  {date_str} 可用性扫描结果")
    print("═" * 50)
    for h in sorted(results.keys()):
        print(f"  {h:02d}:00-{h+1:02d}:00  →  {results[h]}")
    print("═" * 50)
    available = [h for h, v in results.items() if v == "✅ AVAILABLE"]
    if available:
        print(f"\n✅ 可预订时段: {available}")
        print(f"   建议: 改 config.yaml preferences 后跑")
        print(f"   .venv/bin/python reserve.py --date {date_str} --now")
    else:
        print("\n❌ 该日所有时段不可用")
