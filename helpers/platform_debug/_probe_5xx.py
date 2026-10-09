# -*- coding: utf-8 -*-
"""Which request 500s on the investor shell?"""
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.on("response", lambda r: print("  5xx ->", r.status, r.url) if r.status >= 500 else None)
    pg.on("pageerror", lambda e: print("JSERR", str(e)[:200]))
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(1800)
    pg.click(".nav-item[data-mode='estimation']")
    pg.wait_for_timeout(800)
    pg.click(".nav-item[data-mode='history']")
    pg.wait_for_timeout(800)
    pg.click(".nav-item[data-mode='tenders']")
    pg.wait_for_timeout(800)
    b.close()
print("done")
