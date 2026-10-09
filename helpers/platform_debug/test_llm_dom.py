# -*- coding: utf-8 -*-
"""DOM proof: LLM test follows FORM values (not stale saved state)."""
import json
from playwright.sync_api import sync_playwright

out = {}
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.goto("http://127.0.0.1:8077", wait_until="networkidle")
    pg.wait_for_timeout(700)
    pg.click('button[data-tab="llm"]')
    pg.wait_for_timeout(300)
    out["form_base_saved"] = pg.eval_on_selector("#llmBase", "e=>e.value")
    out["form_model_saved"] = pg.eval_on_selector("#llmModel", "e=>e.value")

    # FORM: openai, fake key, unsaved -> 401 mentioning OpenAI (proves form-driven test)
    pg.fill("#llmBase", "https://api.openai.com")
    pg.fill("#llmModel", "gpt-4o-mini")
    pg.fill("#llmKey", "sk-fake-fake")
    pg.click("#llmTest")
    pg.wait_for_timeout(8000)
    out["test_openai_fake_key"] = pg.locator("#llmStatus").inner_text()[:150]

    # FORM: local llama, unsaved -> OK + adopt button
    pg.fill("#llmBase", "http://127.0.0.1:10000")
    pg.fill("#llmModel", "gpt-oss-20b-Q6_K")
    pg.fill("#llmKey", "")
    pg.click("#llmTest")
    pg.wait_for_timeout(6000)
    out["test_local"] = pg.locator("#llmStatus").inner_text()[:150]
    out["adopt_button_count"] = pg.locator("#llmStatus button").count()
    b.close()

print(json.dumps(out, ensure_ascii=False, indent=1))
