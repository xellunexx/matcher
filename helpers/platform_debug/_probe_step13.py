# -*- coding: utf-8 -*-
"""Step 13 verification: exploded storey view (plan §12).

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. explode row visible, slider present (scene has >= 2 real levels)
 2. explodeState(): enabled, levels [0,1,2], t=0 initially
 3. slider -> 100%: walls/elements at level N rise by N * gap, XY preserved;
    level-0 / null-level entries (foundation, roof, site) stay fixed
 4. slider 50%: offsets scale linearly; 0% restores exact base positions
 5. picking still identifies an exploded storey object at its NEW position
 6. no page errors
"""
import json, math, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

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
    out["est_id"] = est_id

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
        pg.wait_for_timeout(3800)  # let the build ladder finish appearing

        # 1. row visible + slider present
        out["explode_row_visible"] = pg.locator("#imExplodeRow").is_visible()
        out["slider_present"] = pg.locator("#imExplodeSlider").count() == 1

        exp = lambda: pg.evaluate("Immersive.spatial3d().explodeState()")
        st0 = exp()
        out["state0"] = st0
        gap = st0["gap"]
        levels = st0["levels"]

        deb = lambda: {e["id"]: e for e in pg.evaluate("Immersive.spatial3d().debugEntries()")}
        base = deb()

        # 3. full explosion
        pg.evaluate("""(()=>{const s=document.getElementById('imExplodeSlider');
            s.value=100; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        pg.wait_for_timeout(300)
        st1 = exp()
        now = deb()
        rise_ok, fixed_ok, xy_ok = True, True, True
        detail = []
        for oid, e in now.items():
            b0 = base[oid]
            lev = e["lvl"]
            expect = levels.index(lev) * gap if (lev is not None and levels.index(lev) > 0) else 0
            dy = round(e["y"] - b0["y"], 3)
            dxz = max(abs(e["x"] - b0["x"]), abs(e["z"] - b0["z"]))
            if abs(dy - expect) > 1e-3:
                rise_ok = False
                detail.append({"id": oid, "lev": lev, "dy": dy, "expect": expect})
            if (expect == 0) and dy != 0: fixed_ok = False
            if dxz > 1e-6: xy_ok = False
        out["full"] = {"t": st1["t"], "rise_ok": rise_ok, "fixed_ok": fixed_ok,
                       "xy_preserved": xy_ok, "gap": gap,
                       "bad": detail[:5], "val_text": pg.locator("#imExplodeVal").inner_text()}

        # 4a. linearity at 50%
        pg.evaluate("""(()=>{const s=document.getElementById('imExplodeSlider');
            s.value=50; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        pg.wait_for_timeout(300)
        half = deb()
        lin_ok = True
        for oid, e in half.items():
            lev = e["lvl"]
            expect = (levels.index(lev) * gap * 0.5) if (lev is not None and levels.index(lev) > 0) else 0
            if abs((e["y"] - base[oid]["y"]) - expect) > 1e-3:
                lin_ok = False
                break
        out["half_linear"] = lin_ok

        # 5. picking an exploded storey object at its NEW projected position:
        # sample geometry-aware surface points (polygon edge midpoints, slightly
        # inset toward the centroid) at the raised z. The roof lets light
        # through to the wall's core height after explosion, so the pick
        # contract can legally return the roof at the wall's vertical center —
        # the wall's own face points must still identify the wall.
        wall2 = next((o for o in sv2["objects"] if o["id"] == "bldg-2.wall-ext.l2"), None)
        assert wall2, "no l2 wall"
        g = wall2["geometry"]
        lev2 = levels.index(2) if 2 in levels else 1
        dz = levels[lev2] * gap * 0.5  # currently exploded at 50%
        pts = []
        if g.get("type") == "extrude":
            poly = g.get("polygon_m") or []
            zc = (float(g.get("z0_m")) + float(g.get("z1_m"))) / 2 + dz
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            edges = []
            for i in range(len(poly)):
                a, b_ = poly[i], poly[(i + 1) % len(poly)]
                edges.append((math.hypot(b_[0]-a[0], b_[1]-a[1]), a, b_))
            edges.sort(key=lambda e: -e[0])
            for L, a, b_ in edges[:6]:
                mx, my = (a[0]+b_[0])/2, (a[1]+b_[1])/2
                pts.append((mx + (cx-mx)*0.05, my + (cy-my)*0.05, zc))
        else:
            c = g.get("center_m"); s = g.get("size_m") or [1, 1, 1]
            cx, cy, cz = [float(v) for v in c]
            zc = cz + dz
            pts = [(cx + float(s[0]) * 0.45, cy, zc), (cx - float(s[0]) * 0.45, cy, zc),
                   (cx, cy + float(s[1]) * 0.45, zc), (cx, cy - float(s[1]) * 0.45, zc)]
        pg.wait_for_timeout(150)
        pick_ok = False
        seen = []
        for pt in pts:
            p2 = pg.evaluate("Immersive.spatial3d().project(%s)" % json.dumps(list(pt)))
            if not p2:
                continue
            ud = pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % (p2[0], p2[1]))
            seen.append(ud and ud.get("spatialId"))
            if ud and ud.get("spatialId") == "bldg-2.wall-ext.l2":
                pick_ok = True
                break
        out["pick_exploded"] = {"expected": "bldg-2.wall-ext.l2", "ok": pick_ok, "seen": seen}

        # 4b. back to 0% restores exact base positions
        pg.evaluate("""(()=>{const s=document.getElementById('imExplodeSlider');
            s.value=0; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        pg.wait_for_timeout(300)
        zero = deb()
        out["restored"] = all(abs(e["y"] - base[oid]["y"]) < 1e-6 for oid, e in zero.items())
        out["state_back"] = exp()["t"]

        out["page_errors"] = errs
        b.close()

    ok = (out["explode_row_visible"] and out["slider_present"]
          and out["state0"]["enabled"] is True and out["state0"]["t"] == 0
          and len(out["state0"]["levels"]) >= 2
          and out["full"]["t"] == 100 and out["full"]["rise_ok"]
          and out["full"]["fixed_ok"] and out["full"]["xy_preserved"]
          and out["full"]["val_text"] == "100%"
          and out["half_linear"]
          and out["pick_exploded"]["ok"]
          and out["restored"] and out["state_back"] == 0
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
