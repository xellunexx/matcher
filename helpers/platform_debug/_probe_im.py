# -*- coding: utf-8 -*-
"""Probe v4: dump .imL layer --yi / computed translate after EXPLODE click."""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8078"

import urllib.request
urllib.request.urlopen(urllib.request.Request(BASE + "/api/estimation/clear", data=b"{}", method="POST"), timeout=15)
picks = sorted(Path("data/demo/processed/files/600799").glob("user_upload_*.xlsx"))

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1500, "height": 900})
    page.goto(BASE, wait_until="networkidle")
    page.click('[data-mode="estimation"]')
    page.wait_for_selector("#estFiles", state="attached", timeout=15000)
    page.set_input_files("#estFiles", [str(x.resolve()) for x in picks])
    page.click("#startProjectAnalysis")
    page.wait_for_selector("#drawer.open #estRun", timeout=120000)
    page.click("#estRun")
    page.wait_for_selector(".im-root", timeout=180000)
    page.click("#drawerClose")
    print("mounted")

    # open first element's explode view, mirroring the proof
    page.evaluate("""()=>{const id=(window.Immersive&&Immersive.model&&Immersive.model.elements[0]||{}).id;
                     window.ImmersiveInspector.openElement(id);}""")
    time.sleep(0.6)
    page.evaluate("document.querySelector('[data-insp-explode]').click()")
    time.sleep(1.2)
    dump = page.evaluate("""()=>{
      const root=document.querySelector('#imRoot');
      const rows=[];
      document.querySelectorAll('.imL').forEach(g=>{
        const cs=getComputedStyle(g);
        rows.push({bucket:g.getAttribute('data-bucket'), cls:g.className.baseVal||g.className,
                   yi:g.style.getPropertyValue('--yi'), tr:cs.translate});
      });
      return {exploded: root&&root.classList.contains('im-exploded'), layers: rows};
    }""")
    print("im-exploded:", dump["exploded"])
    for r in dump["layers"]:
        print(f"  bucket={r['bucket']!r:22} yi={r['yi']!r:6} translate={r['tr']!r}  cls={r['cls']}")
    b.close()
    print("PROBE_DONE")
