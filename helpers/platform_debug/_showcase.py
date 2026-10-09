# -*- coding: utf-8 -*-
"""Show-the-work screenshots: key surfaces of the rebooted app."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(__file__).resolve().parent / "trace" / "showcase"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8077"


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg.goto(BASE, wait_until="networkidle", timeout=45000)
        pg.wait_for_timeout(1400)
        pg.screenshot(path=str(OUT / "1_tenders.png"))

        pg.locator('.tender-card[data-id="564602"]').click()
        pg.wait_for_timeout(1600)
        pg.screenshot(path=str(OUT / "2_tender_564602_review.png"))

        pg.locator('[data-hub="risk"]').click()
        pg.wait_for_timeout(800)
        pg.screenshot(path=str(OUT / "3_risk_hub.png"))
        pg.locator("#drawerClose").click()
        pg.wait_for_timeout(400)

        pg.locator('[data-mode="estimation"]').click()
        pg.wait_for_timeout(900)
        pg.screenshot(path=str(OUT / "4_estimation.png"))

        pg.locator("#hubButton").click()
        pg.wait_for_timeout(900)
        pg.screenshot(path=str(OUT / "5_llm_hub.png"))
        pg.keyboard.press("Escape")

        m = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mp = m.new_page()
        mp.goto(BASE, wait_until="networkidle", timeout=45000)
        mp.wait_for_timeout(1000)
        mp.screenshot(path=str(OUT / "6_mobile_390.png"))
        browser.close()
    print("screenshots ->", OUT)


if __name__ == "__main__":
    main()
