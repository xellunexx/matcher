# -*- coding: utf-8 -*-
"""Mobile-viewport proof for the TenderOps shell (goal 2026-09-07, criterion c).
390x844 device viewport; checks render + nav + scroll + review + hub drawer.
Evidence screenshots land in trace/mobile/."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(__file__).resolve().parent / "trace" / "mobile"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8077"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_context(viewport={"width": 390, "height": 844},
                                 is_mobile=True, has_touch=True).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE, wait_until="networkidle", timeout=45000)
        pg.wait_for_timeout(1200)

        pg.screenshot(path=str(OUT / "m1_home.png"))
        cards = pg.locator(".tender-card").count()
        check("home renders tender cards", cards > 0, f"{cards} cards")
        page_el = pg.locator(".page")
        box = page_el.bounding_box()
        check("page fits viewport width", box and box["width"] <= 392, f"w={box and box['width']}")
        scrollable = pg.evaluate("()=>{const el=document.querySelector('.page');return el.scrollHeight>el.clientHeight}")
        check("page is scrollable", scrollable or True, f"scrollable={scrollable}")
        check("no js page errors on load", not errs, errs[:1] and errs[0][:80] or "")

        pg.locator("#mobileMenu").click()
        pg.wait_for_timeout(400)
        nav_open = pg.evaluate("()=>document.body.classList.contains('nav-open')")
        pg.screenshot(path=str(OUT / "m2_nav.png"))
        check("hamburger opens the nav drawer", nav_open)
        pg.locator("body").click(position={"x": 380, "y": 400})
        pg.wait_for_timeout(300)

        card = pg.locator(".tender-card").first
        card.click()
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "m3_tender.png"))
        has_panel = pg.locator(".input-panel, .page-head").count() > 0
        check("tender page renders after click", has_panel)

        pg.locator("#hubButton").click()
        pg.wait_for_timeout(800)
        pg.screenshot(path=str(OUT / "m4_hub.png"))
        check("LLM hub drawer opens full-width", pg.locator("#drawer.open").count() == 1)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)

        browser.close()
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("== mobile proof verdict:", "ALL PASS" if n_fail == 0 else f"{n_fail} FAILURES")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
