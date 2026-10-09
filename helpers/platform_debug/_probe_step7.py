# -*- coding: utf-8 -*-
"""Step 7 verification: multi-building focus, per-building cost summary, keep-site toggle.
Two-house pack on :8123 (server-side by_building: bldg-1 546082, bldg-2 448936).
All site objects are parented to bldg-1 by the backend wiring (primary building
owns the site plot) — keep-site discriminates under B2 focus.
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def pick_ids(pg):
    r = pg.evaluate("""(()=>{const h=Immersive.spatial3d();if(!h)return null;
      const box=document.querySelector('#imStage canvas').getBoundingClientRect();
      const ids=new Set();
      for(let iy=0; iy<18; iy++)for(let ix=0; ix<26; ix++){
        const p=h.pick(box.x+box.width*(ix+0.5)/26, box.y+box.height*(iy+0.5)/18);
        if(p&&p.spatialId)ids.add(p.spatialId);}
      return [...ids].sort();})()""")
    return set(r) if r else set()

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = hist["history"][0]["id"]
    sp = http("/api/estimation/spatial?id=" + est_id)
    ca = ((sp.get("scene_v2") or {}).get("cost_allocation")) or {}
    byb = ca.get("by_building") or {}
    out["est_id"] = est_id
    out["by_building"] = byb

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(500)
        pg.evaluate("setMode('estimation')")
        pg.evaluate(f"openEstimationRun({json.dumps(est_id)})")
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(2800)

        blds = pg.locator("#imBldChips [data-imf-bld]")
        out["bld_chip_labels"] = blds.all_inner_texts()
        out["bld_chip_ids"] = pg.evaluate("[...document.querySelectorAll('#imBldChips [data-imf-bld]')].map(b=>b.getAttribute('data-imf-bld'))")

        pg.evaluate("[...document.querySelectorAll('#imBldChips [data-imf-bld]')][1].click()")
        pg.wait_for_timeout(600)
        out["after_b1"] = {
            "sum_text": pg.locator("#imBldSum").inner_text() if pg.locator("#imBldSum").count() else None,
            "sum_visible": pg.locator("#imBldSum").is_visible() if pg.locator("#imBldSum").count() else False,
            "keepsite_visible": pg.locator("#imKeepSite").is_visible() if pg.locator("#imKeepSite").count() else False,
            "pick": sorted(pick_ids(pg)),
        }
        pg.evaluate("[...document.querySelectorAll('#imBldChips [data-imf-bld]')][2].click()")
        pg.wait_for_timeout(600)
        out["after_b2"] = {
            "sum_text": pg.locator("#imBldSum").inner_text() if pg.locator("#imBldSum").count() else None,
            "pick": sorted(pick_ids(pg)),
        }
        out["ks_after_b2"] = pg.evaluate("(()=>{const h=Immersive.spatial3d();return h.viewState();})()")
        pg.evaluate("document.getElementById('imKeepSite')?.click()")
        pg.wait_for_timeout(500)
        out["after_b2_ksoff"] = {"pick": sorted(pick_ids(pg))}
        pg.evaluate("document.getElementById('imKeepSite')?.click()")
        pg.wait_for_timeout(400)
        pg.evaluate("[...document.querySelectorAll('#imBldChips [data-imf-bld]')][0].click()")
        pg.wait_for_timeout(600)
        out["after_all"] = {
            "sum_visible": pg.locator("#imBldSum").is_visible() if pg.locator("#imBldSum").count() else False,
            "pick": sorted(pick_ids(pg)),
        }
        out["errors"] = list(errs)
        b.close()

    p1 = set(out["after_b1"]["pick"]); p2 = set(out["after_b2"]["pick"]); pa = set(out["after_all"]["pick"])
    pk = set(out["after_b2_ksoff"]["pick"])
    ks_state = out.get("ks_after_b2") or {}
    b1sum = (out["after_b1"]["sum_text"] or "").replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    checks = [
        ("two building chips + All", len(out["bld_chip_ids"]) == 3),
        ("B1 focus: bldg-2 gone", not any(i.startswith("bldg-2") for i in p1)),
        ("B1 focus: bldg-1 members + own site remain", any(i.startswith("bldg-1") for i in p1) and any(i.startswith("site-1.") for i in p1)),
        ("B1 sum shows by_building value", bool(out["after_b1"]["sum_text"]) and str(int(byb.get("bldg-1", 0)))[:-2] in b1sum),
        ("B1 keep-site toggle visible", out["after_b1"]["keepsite_visible"] is True),
        ("B2 (site on): bldg-2 members stay, bldg-1 fabric gone",
         any(i.startswith("bldg-2") for i in p2) and not any(i.startswith("bldg-1") and not i.endswith(".site.l0") for i in p2)),
        ("keepSite ON by default", ks_state.get("keepSite") is True),
        ("B2 + keepSite OFF: site context drops, bldg-2 stays",
         all(not (i.startswith("site-1.") or i.endswith(".site.l0")) for i in pk) and any(i.startswith("bldg-2") for i in pk)),
        ("B2 sum differs from B1", out["after_b2"]["sum_text"] != out["after_b1"]["sum_text"]),
        ("All restores both + hides sum", out["after_all"]["sum_visible"] is False and any(i.startswith("bldg-1") for i in pa) and any(i.startswith("bldg-2") for i in pa)),
        ("no JS errors", not out["errors"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
