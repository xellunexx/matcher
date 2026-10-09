# -*- coding: utf-8 -*-
"""Reproduce: approve a blocker -> must show as approved (Gate-1 SQLite regression)."""
import json
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.on("pageerror", lambda e: print("JSERR", e))
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(700)
    pg.click(".tcard[data-id='605862']")
    pg.wait_for_timeout(600)
    pg.click('button[data-tab="blockers"]')
    pg.wait_for_timeout(400)
    out["blockers_total"] = pg.locator("[data-resolve]").count()
    out["resolved_before"] = pg.locator("#tab-blockers .tag.pass").count()
    if out["blockers_total"]:
        pg.locator("[data-resolve]").first.click()
        pg.wait_for_timeout(300)
        pg.fill("#mNote", "проверка: цената потвърдена от доставчик Иванов ЕТ")
        pg.click("#mOk")
        pg.wait_for_timeout(1200)
        out["resolved_after"] = pg.locator("text=разрешен").count()
        out["badge_after"] = pg.locator("#blkCount").inner_text()
    out["js_ok"] = True
    b.close()

import sqlite3
con = sqlite3.connect(r"dist\tenderops.sqlite3")
out["db_rows"] = con.execute("SELECT COUNT(*) FROM human_resolutions").fetchone()[0]
out["db_latest"] = con.execute("SELECT tender_id,boq_key,note,actor,at FROM human_resolutions ORDER BY id DESC LIMIT 1").fetchall()
con.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
