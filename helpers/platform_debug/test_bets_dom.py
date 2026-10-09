# -*- coding: utf-8 -*-
"""Bets verification: evidence badge+modal, ⌘K palette, costdb search, what-if, outcome."""
import json
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(700)

    # A: evidence badge + WHY modal on processed tender
    pg.click(".tcard[data-id='605862']")
    pg.wait_for_timeout(700)
    pg.click('button[data-tab="boq"]')
    pg.wait_for_timeout(400)
    out["badges"] = pg.locator(".evb").count()
    pg.locator(".evb").first.click()
    pg.wait_for_timeout(400)
    out["why_title"] = pg.locator("#mTitle").inner_text()[:80]
    out["why_has_level"] = "Ниво" in pg.locator("#mBody").inner_text()
    pg.click("#btnAudit")
    pg.wait_for_timeout(1000)
    out["audit_area_text"] = pg.locator("#evAudit").inner_text()[:100]
    pg.click("#mCancel")
    pg.wait_for_timeout(200)

    # C: palette
    pg.keyboard.press("Control+k")
    pg.wait_for_timeout(300)
    out["palette_open"] = pg.locator("#palette").is_visible()
    pg.fill("#palQ", "бетон")
    pg.wait_for_timeout(300)
    out["palette_items"] = pg.locator("#palList .pal").count()
    out["palette_last_hint"] = pg.locator("#palList .pal").last.inner_text().replace("\n", " ")[:60]
    # run costdb search
    items = pg.locator("#palList .pal").all_inner_texts()
    pg.fill("#palQ", "цени бетон")
    pg.wait_for_timeout(300)
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(4500)
    out["costdb_modal_rows"] = pg.locator("#mBody > div").count()
    out["costdb_title"] = pg.locator("#mTitle").inner_text()[:40]
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(250)

    # E: what-if on pricing tab
    pg.click('button[data-tab="pricing"]')
    pg.wait_for_timeout(400)
    out["whatif_present"] = pg.locator("#wiMargin").count() == 1
    out["wi_out_has"] = "Сценарий без ДДС" in pg.locator("#wiOut").inner_text()
    pg.locator("#wiMargin").fill("10")
    pg.wait_for_timeout(250)
    out["wi_after_margin10"] = "11896577" not in pg.locator("#wiOut").inner_text()  # trivial truth: re-rendered

    # D: outcome form present
    out["outcome_form"] = pg.locator("#ocSave").count() == 1

    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
