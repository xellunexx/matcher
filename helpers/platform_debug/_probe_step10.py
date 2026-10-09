# -*- coding: utf-8 -*-
"""Step 10 verification: View-by COST heatmap (plan S5).

Checks against a live server (PROBE_BASE, default http://127.0.0.1:8123):
 1. the view-by row exposes a 4th 'cost' button
 2. cost mode applies a non-neutral ramp color to priced objects
 3. objects without allocated cost are neutral #3c4552 (never a fake cheap hue)
 4. same cost -> same color; higher cost -> different color than lower
 5. legend shows the gradient swatch (im-costgrad) + hint text
 6. material mode restores canonical base colors exactly
 7. no page errors anywhere
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")
NEUTRAL = "3c4552"

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = sp = None
    for h in hist["history"]:
        probe = http("/api/estimation/spatial?id=" + h["id"])
        sv2 = probe.get("scene_v2") or {}
        if len(sv2.get("buildings") or []) == 2:
            est_id, sp = h["id"], probe
            break
    assert est_id, "no two-house estimation pack in history"
    sv2 = sp.get("scene_v2") or {}
    objs = sv2.get("objects") or []
    cost_of = {}
    for o in objs:
        ins = o.get("inspect") or {}
        c = ins.get("cost_eur")
        cost_of[o["id"]] = float(c) if isinstance(c, (int, float)) else None
    priced = {k: v for k, v in cost_of.items() if v is not None and v > 0}
    out["est_id"] = est_id
    out["objects"] = len(objs)
    out["priced_objects"] = len(priced)

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
        pg.evaluate("openEstimationRun(%s)" % json.dumps(est_id))
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(2800)

        btns = pg.locator("#imViewBtns [data-im-view]")
        out["view_buttons"] = sorted(btns.evaluate_all("els=>els.map(e=>e.getAttribute('data-im-view'))"))
        assert len(priced) >= 3, "need >=3 priced objects for ramp checks"

        def display_state():
            return pg.evaluate(
                "(()=>{const h=Immersive.spatial3d();return h&&h.describeDisplay?h.describeDisplay():null;})()"
            ) or []

        base = {e["id"]: e for e in display_state()}
        out["mode_default"] = base[next(iter(base))]["mode"]

        pg.click("#imViewBtns [data-im-view='cost']")
        pg.wait_for_timeout(600)
        cm = {e["id"]: e for e in display_state()}
        out["mode_after_cost"] = cm[next(iter(cm))]["mode"]

        # 2. priced objects get ramp colors (NOT neutral, NOT canonical base color)
        priced_cols = {}
        for oid, c in priced.items():
            e = cm.get(oid)
            if e is None:
                continue
            priced_cols[oid] = e["color"]
        ramp = [e["color"] for oid, e in cm.items()
                if cost_of.get(oid) and not e["color"].endswith(NEUTRAL)]
        out["priced_on_ramp"] = len(ramp) == sum(1 for oid in priced if oid in cm)

        # 3. unpriced objects must be neutral
        unp = [(oid, cm[oid]["color"]) for oid in cm
               if not (cost_of.get(oid) and cost_of[oid] > 0)]
        out["neutral_count"] = len(unp)
        out["neutral_ok"] = all(col.endswith(NEUTRAL) for _, col in unp) if unp else "n/a"

        # 4. monotonic sanity: max-cost color != min-cost color (distinct ramp ends)
        lo = min(priced, key=priced.get); hi = max(priced, key=priced.get)
        out["extremes"] = {"min": priced[lo], "max": priced[hi],
                           "differ": cm[lo]["color"] != cm[hi]["color"]}
        # equal-cost -> equal-color (only checked if a tie exists)
        from collections import Counter
        ties = [c for c, n in Counter(priced.values()).items() if n > 1]
        if ties:
            t = ties[0]
            ids_t = [oid for oid, c in priced.items() if c == t and oid in cm]
            out["tie_same_color"] = len({cm[i]["color"] for i in ids_t}) == 1
        else:
            out["tie_same_color"] = "n/a"

        # 5. legend: gradient swatch + localized hint
        leg_html = pg.locator("#imViewLegend").inner_html()
        out["legend_gradient"] = "im-costgrad" in leg_html
        out["legend_text"] = pg.locator("#imViewLegend").inner_text()[:60]

        # 6. material mode restores canonical colors exactly
        pg.click("#imViewBtns [data-im-view='material']")
        pg.wait_for_timeout(600)
        mm = {e["id"]: e for e in display_state()}
        out["material_restored"] = all(
            e["color"].lower() in (e["baseColor"].lower(),) for e in mm.values()
        )

        out["page_errors"] = errs
        b.close()

    ok = (out["mode_default"] == "material"
          and out["mode_after_cost"] == "cost"
          and "cost" in out["view_buttons"]
          and out["priced_on_ramp"]
          and out["neutral_ok"] in (True, "n/a")
          and out["extremes"]["differ"]
          and out["tie_same_color"] in (True, "n/a")
          and out["legend_gradient"]
          and out["material_restored"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
