# -*- coding: utf-8 -*-
"""What data-id cards does the investor shell list after going back?"""
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.on("pageerror", lambda e: print("JSERR", str(e)[:200]))
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(1300)
    if pg.locator("#backToTenders").count():
        pg.click("#backToTenders")
        pg.wait_for_timeout(700)
    ids = pg.locator("[data-id]").evaluate_all("es => es.map(e => e.dataset.id)")
    print(json.dumps({"ids": ids, "html_head": pg.locator("#page").inner_html()[:300]}, ensure_ascii=False, indent=1))
    b.close()
