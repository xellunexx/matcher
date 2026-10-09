# -*- coding: utf-8 -*-
"""Runtime smoke of the level-up frontend against the dev server (same app/web swap)."""
import json
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("requestfailed", lambda r: errs.append("REQFAIL " + r.url))
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle", timeout=30000)
    pg.wait_for_timeout(1300)
    out["tenders_in_sidebar"] = pg.locator(".tcard").count()
    out["env_label"] = pg.locator("#envVersion").inner_text() if pg.locator("#envVersion").count() else None
    out["tabs"] = pg.locator("#tabs button").count()
    pg.locator(".tcard").first.click() if out["tenders_in_sidebar"] else None
    pg.wait_for_timeout(900)
    out["status_title"] = pg.locator("#statusTitle").inner_text()[:60] if pg.locator("#statusTitle").count() else None
    out["tName"] = pg.locator("#tName").inner_text()[:50] if pg.locator("#tName").count() else None
    out["head_stats"] = pg.locator("#tHead .badge, #tHead .hstat").count()
    pg.keyboard.press("Control+k")
    pg.wait_for_timeout(400)
    out["palette_open"] = pg.locator("#palette").is_visible()
    out["palette_card"] = pg.evaluate("document.querySelector('#palette')?.className")
    pg.fill("#palQ", "блокери")
    pg.wait_for_timeout(350)
    out["pal_items"] = pg.locator("#palList .pal, #palList .p-item, #palList [class*=item]").count()
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    pg.click('button[data-tab="pricing"]') if pg.locator('button[data-tab="pricing"]').count() else None
    pg.wait_for_timeout(400)
    out["whatif"] = pg.locator("#wiMargin, input[type=range]").count()
    pg.screenshot(path="levelup_smoke.png", full_page=False)
    out["errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
