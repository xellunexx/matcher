# -*- coding: utf-8 -*-
"""UI baseline screenshot audit — captures before/after shots of every main screen.

    py -3 _ui_baseline.py [--dir trace/audit/before] [--base http://127.0.0.1:8077]
"""
import argparse
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TENDER = 600799


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="trace/audit/before")
    ap.add_argument("--base", default=os.environ.get("IMMERSIVE_BASE", "http://127.0.0.1:8077"))
    args = ap.parse_args()
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        def shot(name):
            time.sleep(0.6)
            page.screenshot(path=str(out / f"{name}.png"))
            print("shot", name)

        page.goto(args.base, wait_until="networkidle")
        shot("01_tenders_list")
        page.click(f'.tender-card[data-id="{TENDER}"]')
        page.wait_for_selector(".decision-card", timeout=15000)
        shot("02_tender_review")
        page.click('[data-mode="estimation"]')
        # estimation contract (backend schema 2026-09-08): the immersive workspace mounts
        # only from a real estimation run over uploaded evidence (see _immersive_proof.py).
        import urllib.request as _urlreq
        _urlreq.urlopen(_urlreq.Request(args.base + "/api/estimation/clear", data=b"{}",
                                        method="POST"), timeout=15)
        picks = sorted(Path("data/demo/processed/files/600799").glob("user_upload_*.xlsx"))
        page.wait_for_selector("#estFiles", state="attached", timeout=15000)
        page.set_input_files("#estFiles", [str(x.resolve()) for x in picks])
        page.click("#startProjectAnalysis")
        page.wait_for_selector("#drawer.open #estRun", timeout=120000)
        page.click("#estRun")
        page.wait_for_selector(".im-root", timeout=180000)
        page.click("#drawerClose")
        time.sleep(1.5)
        shot("03_immersive_explore")
        page.click('[data-mode="history"]')
        time.sleep(1.0)
        shot("04_history")
        page.click('[data-mode="tenders"]')
        page.click("#hubButton")
        time.sleep(0.8)
        shot("05_hub_drawer")
        page.click("#hubClose") if page.query_selector("#hubClose") else page.keyboard.press("Escape")
        page.click('[data-utility="help"]')
        time.sleep(0.6)
        shot("06_help_modal")
        page.keyboard.press("Escape")
        # mobile
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(args.base, wait_until="networkidle")
        shot("07_mobile_tenders")

        browser.close()
        if errors:
            Path(out / "console_errors.txt").write_text("\n".join(errors), encoding="utf-8")
            print(f"console errors: {len(errors)} (see console_errors.txt)")
        else:
            print("console errors: 0")
    print(f"done -> {out}")


if __name__ == "__main__":
    main()
