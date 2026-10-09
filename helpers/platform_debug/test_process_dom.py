# -*- coding: utf-8 -*-
"""User-path replay: click 605645 card -> Обработи -> watch status until done/error."""
import json, time
from playwright.sync_api import sync_playwright

out = {"events": []}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(700)
    cards = pg.locator(".tcard .tno").all_inner_texts()
    out["sidebar_numbers"] = [c.strip() for c in cards]

    pg.click(".tcard[data-id='605645']")
    pg.wait_for_timeout(500)
    out["has_process_btn"] = pg.locator("#btnProcess").count() == 1
    if out["has_process_btn"]:
        pg.click("#btnProcess")
        t0 = time.time()
        last = ""
        while time.time() - t0 < 240:
            st = pg.locator("#procStatus").inner_text()
            bt = pg.locator("#btnProcess").inner_text()
            if st != last:
                out["events"].append(f"{int(time.time()-t0)}s | btn='{bt[:24]}' | {st[:110]}")
                last = st
            if st.startswith("готово") or st.startswith("Грешка") or st.startswith("Мрежа"):
                break
            pg.wait_for_timeout(1500)
        out["elapsed_s"] = int(time.time() - t0)
        out["final_boq_text"] = None
        if last.startswith("готово"):
            pg.click("#btnProcess")  # 'Преотвори резултата'
            pg.wait_for_timeout(800)
            out["final_boq_len"] = len(pg.locator("#tab-boq").inner_text())
    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
