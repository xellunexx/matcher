# -*- coding: utf-8 -*-
"""Approve R-x on 606060 live and verify the immediate + persisted flip."""
import json, sqlite3
from playwright.sync_api import sync_playwright

con = sqlite3.connect(r"dist\tenderops.sqlite3")
before = con.execute("SELECT COUNT(*) FROM human_resolutions").fetchone()[0]
con.close()

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(800)
    pg.click(".tcard[data-id='606060']")
    pg.wait_for_timeout(800)
    pg.click('button[data-tab="blockers"]')
    pg.wait_for_timeout(400)
    out["open_before"] = pg.locator("[data-resolve]").count()
    out["resolved_before"] = pg.locator("#tab-blockers .tag.pass").count()
    pg.locator("[data-resolve]").first.click()
    pg.wait_for_timeout(300)
    pg.fill("#mNote", "live probe approve")
    pg.click("#mOk")
    pg.wait_for_timeout(1500)
    out["resolved_mid"] = pg.locator("#tab-blockers .tag.pass").count()
    out["badge_mid"] = pg.locator("#blkCount").inner_text()
    # persist probe: reload -> open same tender -> count
    pg.click(".tcard[data-id='605862']")
    pg.wait_for_timeout(400)
    pg.click(".tcard[data-id='606060']")
    pg.wait_for_timeout(900)
    pg.click('button[data-tab="blockers"]')
    pg.wait_for_timeout(500)
    out["resolved_after_navigation"] = pg.locator("#tab-blockers .tag.pass").count()
    b.close()

con = sqlite3.connect(r"dist\tenderops.sqlite3")
out["db_rows_after"] = con.execute("SELECT COUNT(*) FROM human_resolutions").fetchone()[0]
con.close()
out["db_added"] = out["db_rows_after"] - before
print(json.dumps(out, ensure_ascii=False, indent=1))
