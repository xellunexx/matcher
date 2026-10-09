# -*- coding: utf-8 -*-
"""Focused diagnosis: isolate-visibility behavior after drawer action."""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

from playwright.sync_api import sync_playwright
est_id = http("/api/estimation/history")["history"][0]["id"]
with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": 1400, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.goto(BASE + "/", wait_until="networkidle")
    pg.wait_for_timeout(400)
    pg.evaluate("setMode('estimation')")
    pg.evaluate(f"openEstimationRun({json.dumps(est_id)})")
    pg.wait_for_selector("#imStage canvas", timeout=30000)
    pg.wait_for_timeout(2500)

    box = pg.locator("#imStage canvas").first.bounding_box()
    hit = None
    tip = pg.locator(".im-s3dtip")
    for iy in range(8):
        if hit: break
        for ix in range(12):
            x = box["x"] + box["width"] * (ix + 0.5) / 12
            y = box["y"] + box["height"] * (iy + 0.5) / 8
            pg.mouse.move(x, y); pg.wait_for_timeout(80)
            if tip.count() and tip.is_visible():
                hit = {"x": x, "y": y}; break
    if not hit:
        print(json.dumps({"fatal": "no hover hit"})); raise SystemExit(1)

    pg.mouse.click(hit["x"], hit["y"]); pg.wait_for_timeout(600)
    pre = pg.evaluate("""(()=>{const h=Immersive.spatial3d();if(!h)return null;
      return JSON.stringify({sel:h.getSelected(), view:h.viewState()});})()""")
    # isolate via the drawer button
    pg.locator("#drawer [data-spo-isolate]").click(); pg.wait_for_timeout(500)
    post = pg.evaluate(f"""(()=>{{const h=Immersive.spatial3d();if(!h)return null;
      const p=h.pick({hit['x']},{hit['y']});
      const el=document.elementFromPoint({hit['x']},{hit['y']});
      return JSON.stringify({{sel:h.getSelected(), view:h.viewState(),
        pickAtHit:(p&&p.spatialId)||null,
        atPoint: el? (el.tagName+'.'+(el.className&&el.className.baseVal!==undefined?el.className.baseVal:el.className)+'#'+el.id) : null}});}})()""")
    pg.mouse.move(hit["x"], hit["y"]); pg.wait_for_timeout(150)
    out = {"pre": json.loads(pre) if pre else None,
           "post": json.loads(post) if post else None,
           "tip_visible_after_move": tip.is_visible() if tip.count() else False,
           "tip_text": tip.inner_text() if tip.count() and tip.is_visible() else None,
           "errors": errs}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    b.close()
