# -*- coding: utf-8 -*-
"""DOM proof of the user loop: pick tender -> rich result -> process button for unprocessed."""
import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8077"
out = {}

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_timeout(800)

    # 1) processed registry tender renders rich tabs
    pg.click(".tcard[data-id='605862']")
    pg.wait_for_timeout(600)
    out["605862_boq_rows"] = pg.locator("#tab-boq tbody tr").count()
    out["605862_blk_badge"] = pg.locator("#blkCount").inner_text()
    out["605862_head_offer"] = pg.locator("#tHead .badge").nth(1).inner_text().replace("\n", " ")

    # 2) unprocessed registry tender shows the process CTA
    pg.click(".tcard[data-id='584256']")
    pg.wait_for_timeout(400)
    out["584256_has_process_btn"] = pg.locator("#btnProcess").count() == 1
    out["584256_btn_text"] = pg.locator("#btnProcess").inner_text() if pg.locator("#btnProcess").count() else ""

    # 3) demo tender still fine
    pg.click(".tcard[data-id='601701']")
    pg.wait_for_timeout(600)
    out["601701_boq_rows"] = pg.locator("#tab-boq tbody tr").count()

    # 4) lump-sum pack (0 rows) renders without JS crash
    pg.click(".tcard[data-id='605645']")
    pg.wait_for_timeout(400)
    out["605645_boq_msg"] = pg.locator("#tab-boq").inner_text()[:80]

    out["js_errors"] = errs
    b.close()

print(json.dumps(out, ensure_ascii=False, indent=1))
