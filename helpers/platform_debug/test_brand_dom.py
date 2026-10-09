# -*- coding: utf-8 -*-
"""Brand + offer button DOM check."""
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
    pg.wait_for_timeout(600)
    out["logo_img"] = pg.locator(".logo img").count()
    out["favicon"] = pg.locator("head link[rel=icon]").get_attribute("href") if pg.locator("head link[rel=icon]").count() else None
    out["brand_sub"] = pg.locator(".brand-sub").inner_text()
    out["logo_200"] = pg.evaluate("fetch('/logo.svg').then(r=>r.status)")
    pg.click(".tcard[data-id='605862']")
    pg.wait_for_timeout(700)
    out["offer_btn"] = pg.locator("a[href$='offer.docx']").count()
    out["offer_href"] = pg.locator("a[href$='offer.docx']").get_attribute("href") if out["offer_btn"] else None
    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
