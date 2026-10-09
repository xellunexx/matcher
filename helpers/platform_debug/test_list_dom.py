# -*- coding: utf-8 -*-
"""Sidebar DOM proof: order, 601701 price kept, expired flagged."""
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
    cards = pg.locator(".tcard").all()
    out["order"] = [c.get_attribute("data-id") for c in cards]
    row_601701 = pg.locator(".tcard[data-id='601701']").inner_text()
    out["601701_has_offer_price"] = "239 393" in row_601701.replace("\xa0", " ")
    expired = pg.locator(".tcard[data-id='576239']").inner_text()
    out["576239_expired_badge"] = "изтекла" in expired
    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
