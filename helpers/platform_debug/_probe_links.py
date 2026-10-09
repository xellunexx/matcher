# -*- coding: utf-8 -*-
"""Focused: are the output anchors in the levelup overview DOM?"""
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(1000)
    pg.click(".tcard[data-id='601701']")
    pg.wait_for_timeout(1200)
    out = {
        "a_offer": pg.locator("a[href$='offer.docx']").count(),
        "a_zip": pg.locator("a[href$='submission.zip']").count(),
        "page_actions_html": pg.locator(".page-actions").inner_html()[:500] if pg.locator(".page-actions").count() else None,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    b.close()
