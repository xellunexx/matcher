# -*- coding: utf-8 -*-
"""Cancel-path proof: Обработи -> button becomes Спри -> confirm modal -> run cancelled, no pack."""
import json, os, time
from playwright.sync_api import sync_playwright

PACK = r"dist\data\demo\processed\605645.json"
# fresh fixture: cancel proof needs an UNprocessed tender
if os.path.exists(PACK):
    os.remove(PACK)
out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(700)
    pg.click(".tcard[data-id='605645']")
    pg.wait_for_timeout(400)
    out["btn_initial"] = pg.locator("#btnProcess").inner_text()
    pg.click("#btnProcess")
    pg.wait_for_timeout(1500)
    out["btn_while_running"] = pg.locator("#btnProcess").inner_text()
    pg.click("#btnProcess")            # Спри
    pg.wait_for_timeout(300)
    out["modal_visible"] = pg.locator("#modal").is_visible()
    out["modal_title"] = pg.locator("#mTitle").inner_text()
    pg.click("#mOk")                   # confirm stop
    t0 = time.time()
    final = ""
    while time.time() - t0 < 60:
        final = pg.locator("#procStatus").inner_text()
        if final.startswith(("Спряна", "Грешка", "готово")):
            break
        pg.wait_for_timeout(700)
    out["final_status"] = final
    out["btn_final"] = pg.locator("#btnProcess").inner_text()
    out["pack_written"] = os.path.exists(PACK)
    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
