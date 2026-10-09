# -*- coding: utf-8 -*-
"""Level-up frontend suite — same product truths, new cockpit selectors."""
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
    pg.wait_for_timeout(1000)
    out["tenders"] = pg.locator(".tcard").count()
    out["tabs"] = pg.locator("#tabs button").count()
    out["env_label"] = pg.locator("#envVersion").inner_text() if pg.locator("#envVersion").count() else None

    pg.click(".tcard[data-id='601701']")
    pg.wait_for_timeout(900)
    out["hero_present"] = pg.locator(".command-hero").count() == 1
    pg.click('button[data-tab="pricing"]')
    pg.wait_for_timeout(400)
    out["whatif"] = pg.locator("#wiMargin").count() == 1

    pg.click(".tcard[data-id='605862']")
    pg.wait_for_timeout(800)
    pg.click('button[data-tab="blockers"]')
    pg.wait_for_timeout(450)
    out["blockers_open"] = pg.locator("[data-resolve]").count()
    out["pills"] = [pg.locator(".status-pill").nth(i).inner_text() for i in range(2)]
    pg.locator("[data-resolve]").first.click()
    pg.wait_for_timeout(400)
    out["modal_note_box"] = pg.locator("#resolveNote").count() == 1
    pg.fill("#resolveNote", "potвърдено чрез оферта от доставчик")
    pg.click("#mOk")
    pg.wait_for_timeout(900)
    out["resolved_cards"] = pg.locator(".blocker.resolved").count()
    out["pills_after"] = pg.locator(".status-pill").first.inner_text()

    pg.click('button[data-tab="boq"]')
    pg.wait_for_timeout(400)
    out["evidence_dots"] = pg.locator(".evidence-dot").count()
    pg.locator(".evidence-dot").first.click()
    pg.wait_for_timeout(450)
    out["evidence_modal"] = "Защо тази цена" in pg.locator("#mTitle").inner_text()
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(250)
    out["modal_closed"] = not pg.locator("#modal").is_visible()

    pg.keyboard.press("Control+k")
    pg.wait_for_timeout(300)
    out["palette_open"] = pg.locator("#palette").is_visible()
    pg.fill("#palQ", "блокери")
    pg.wait_for_timeout(300)
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(400)
    out["after_palette_tab"] = pg.evaluate("document.querySelector('#tabs button.active')?.dataset.tab")

    out["offer_links"] = pg.locator("a[href$='offer.docx']").count()
    out["submission_links"] = pg.locator("a[href$='submission.zip']").count()

    out["js_errors"] = errs
    b.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
