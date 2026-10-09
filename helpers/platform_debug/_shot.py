import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
import time
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(viewport={"width": 1600, "height": 1000}, locale="bg-BG").new_page()
    pg.goto("http://127.0.0.1:8077", wait_until="domcontentloaded")
    time.sleep(2.0)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_main.png", full_page=False)
    pg.click("button[data-tab='boq']"); time.sleep(0.4)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_boq.png", full_page=False)
    pg.click("button[data-tab='blockers']"); time.sleep(0.4)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_blockers.png", full_page=False)
    pg.click("button[data-tab='pricing']"); time.sleep(0.4)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_pricing.png", full_page=False)
    pg.click("button[data-tab='clarifs']"); time.sleep(0.3)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_clarifs.png", full_page=False)
    pg.click("button[data-tab='compliance']"); time.sleep(0.3)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_compliance.png", full_page=False)
    pg.click("button[data-tab='trace']"); time.sleep(0.6)
    pg.screenshot(path=r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\trace\shot_trace.png", full_page=False)
    b.close()
print("shots saved")
