import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(viewport={"width": 1600, "height": 1000}, locale="bg-BG").new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8077", wait_until="domcontentloaded"); time.sleep(2)
    # click the registry tender card
    cards = pg.eval_on_selector_all("#tenderList .tcard", "els => els.map(e => ({id: e.dataset.id, name: e.innerText.slice(0,40)}))")
    print("sidebar cards:", len(cards))
    for c in cards:
        print("  ", c["id"], c["name"])
    pg.click("#tenderList .tcard[data-id='605718']"); time.sleep(0.6)
    print("--- overview ---")
    print(pg.evaluate("() => document.querySelector('#tab-overview').innerText")[:800])
    print("--- docs ---")
    print(pg.evaluate("() => document.querySelector('#tab-docs').innerText")[:400])
    print("JS errors:", errs if errs else "none")
    pg.click("button[data-tab='llm']"); time.sleep(0.6)
    llm = pg.evaluate("() => document.querySelector('#tab-llm').innerText")
    print("--- LLM tab ---")
    print(llm[:800])
    b.close()
