# -*- coding: utf-8 -*-
"""Sniff the public ЦАИС /today page to discover its search API."""
import json
from playwright.sync_api import sync_playwright

calls = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    def on_req(req):
        if "service.eop.bg" in req.url:
            calls.append({"url": req.url, "method": req.method,
                          "post": (req.post_data or "")[:600]})
    pg.on("request", on_req)
    pg.goto("https://app.eop.bg/today", wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2500)
    # type a search term to trigger the search call
    try:
        inp = pg.locator("input[type='text'], input[placeholder]").first
        inp.fill("детска площадка")
        inp.press("Enter")
        pg.wait_for_timeout(3500)
    except Exception as e:
        calls.append({"err": str(e)})
    b.close()

seen = set()
for c in calls:
    key = (c.get("url"), c.get("post", "")[:80])
    if key in seen:
        continue
    seen.add(key)
    print(json.dumps(c, ensure_ascii=False)[:700])
