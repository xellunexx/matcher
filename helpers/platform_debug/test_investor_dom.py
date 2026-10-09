# -*- coding: utf-8 -*-
"""Investor UI journey (locked pack): 3 modes, intake, upload, review gate, history."""
import json
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(1200)
    out["nav_modes"] = pg.locator(".nav-item").count()
    out["brand"] = pg.locator(".brand-name").inner_text() if pg.locator(".brand-name").count() else None
    out["hero_intake"] = pg.locator("#tenderRef").count() == 1
    # the app boots into the last-worked tender's review — back up to the rail first
    if pg.locator("#backToTenders").count():
        pg.click("#backToTenders")
        pg.wait_for_timeout(700)
    out["tender_rows"] = pg.locator("[data-id]").count()
    # select a processed tender: review hub shows
    pg.locator("[data-id='605862']").first.click()
    pg.wait_for_timeout(1600)
    out["page_title"] = pg.locator(".page-title").inner_text()[:60] if pg.locator(".page-title").count() else None
    out["review_hubs"] = pg.locator("[data-hub]").count()
    out["generate_disabled_initial"] = pg.locator("#generateDocs[disabled]").count() == 1
    for h in ("project", "price", "risk", "requirements", "evidence"):
        pg.locator(f"[data-hub='{h}']").click()
        pg.wait_for_timeout(280)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(160)
    pg.wait_for_timeout(300)
    out["generate_enabled_after_review"] = pg.locator("#generateDocs[disabled]").count() == 0
    blockers_open = pg.locator("[data-resolve]").count()
    out["blockers_open_tabs"] = blockers_open
    if blockers_open:
        out["generate_still_disabled"] = pg.locator("#generateDocs[disabled]").count() == 1
    # estimation mode
    pg.locator(".nav-item[data-mode='estimation']").click()
    pg.wait_for_timeout(500)
    out["estimation_stage"] = pg.locator(".estimation-stage, .estimation-landing").count() >= 1
    # history mode
    pg.locator(".nav-item[data-mode='history']").click()
    pg.wait_for_timeout(500)
    out["history_render"] = pg.locator(".history-list, .empty-state").count() >= 1
    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
