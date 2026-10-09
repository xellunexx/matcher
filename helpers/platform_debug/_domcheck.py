import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(viewport={"width": 1600, "height": 1000}, locale="bg-BG").new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto("http://127.0.0.1:8077", wait_until="domcontentloaded"); time.sleep(2)
    for tab in ["overview", "docs", "boq", "blockers", "pricing", "compliance", "clarifs", "trace"]:
        pg.click(f"button[data-tab='{tab}']"); time.sleep(0.25)
        txt = pg.evaluate("() => document.querySelector('#tab-%s').innerText" % tab)
        print(f"[{tab}] chars={len(txt)} | first: {txt.strip()[:90]!r}")
    # quick DOM stats on boq
    pg.click("button[data-tab='boq']"); time.sleep(0.2)
    n = pg.evaluate("() => document.querySelectorAll('#boqTable tbody tr').length")
    print("boq rows:", n)
    print("JS errors:", errs if errs else "none")
    b.close()
