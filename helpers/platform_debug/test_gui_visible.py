# -*- coding: utf-8 -*-
"""Visible-browser proof: same steps as headless suite, in a real window the user sees."""
import json, time
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=False, slow_mo=300)
    pg = b.new_page(viewport={"width": 1500, "height": 950})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(900)

    pg.click(".tcard[data-id='606060']")
    pg.wait_for_timeout(800)
    out["606060_boq_rows"] = pg.locator("#tab-boq tbody tr").count()
    out["606060_blk_badge"] = pg.locator("#blkCount").inner_text()
    out["606060_offer"] = pg.locator("#tHead .badge").nth(1).inner_text().replace("\n", " ")
    out["606060_blk_sample"] = pg.locator("#tab-blockers .card").first.inner_text()[:90] if pg.locator("#tab-blockers .card").count() else ""
    pg.click('button[data-tab="pricing"]')
    pg.wait_for_timeout(400)
    out["606060_pricing_visible"] = "Общо с ДДС" in pg.locator("#tab-pricing").inner_text()

    pg.click(".tcard[data-id='601701']")
    pg.wait_for_timeout(700)
    out["601701_boq_rows"] = pg.locator("#tab-boq tbody tr").count()

    out["js_errors"] = errs
    # hold the result open for the user to see with their own eyes
    pg.wait_for_timeout(15000)
    b.close()

print(json.dumps(out, ensure_ascii=False, indent=1))
