# -*- coding: utf-8 -*-
"""Reproduce against the tender the user actually worked: 606060 (251 blockers)."""
import json
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(800)
    n = pg.locator(".tcard[data-id='606060']").count()
    out["card606060_present"] = n
    if not n:
        pg.screenshot(path="dbg60.png")
        out["sidebar_ids"] = pg.locator(".tcard").evaluate_all("es => es.map(e => e.dataset.id)")
    else:
        pg.click(".tcard[data-id='606060']")
        pg.wait_for_timeout(700)
        pg.click('button[data-tab="blockers"]')
        pg.wait_for_timeout(500)
        out["blockers_open"] = pg.locator("[data-resolve]").count()
        out["resolved_cards"] = pg.locator("#tab-blockers .tag.pass").count()
        out["badge"] = pg.locator("#blkCount").inner_text()
    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
