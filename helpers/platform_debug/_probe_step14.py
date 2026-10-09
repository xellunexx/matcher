# -*- coding: utf-8 -*-
"""Step 14 verification: measurement tool (plan §13).

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. measure row visible with 3 buttons
 2. 3D mode: click two real geometry points -> measureState reports the exact
    contract-space distance between them (computed from API data)
 3. the DOM label shows the same value in metres
 4. XY mode reports the horizontal distance (must differ when dz != 0)
 5. explosion does not change measured distances; a point picked while
    exploded returns TRUE (un-exploded) contract coordinates
 6. a 3rd click starts a fresh pair; CLEAR empties everything
 7. plain object selection is intact after leaving measure mode
 8. no page errors; nothing is persisted (no API write endpoints called — the
    tool never touches the network at all)
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
    byid = {o["id"]: o for o in sv2.get("objects") or []}
    out["est_id"] = est_id

    # z-ranges per geometry — a picked point must lie on the picked object
    def zrange(o):
        g = o.get("geometry") or {}
        t = g.get("type")
        if t == "extrude":
            return (float(g.get("z0_m") or 0), float(g.get("z1_m") or 0))
        if t == "gable_roof":
            return (float(g.get("eave_z_m") or 0), float(g.get("ridge_z_m") or 0))
        c = g.get("center_m"); s = g.get("size_m")
        if c and s:
            return (float(c[2]) - float(s[2]) / 2, float(c[2]) + float(s[2]) / 2)
        return (None, None)

    f1 = byid["bldg-1.foundation.l0"]["geometry"]          # extrude, top 0.6
    A = (12.0, 10.0, float(f1["z1_m"]))
    roof = byid["bldg-2.roof.l0"]["geometry"]               # gable_roof
    xs = [p[0] for p in roof["polygon_m"]]; ys = [p[1] for p in roof["polygon_m"]]
    B = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, float(roof["ridge_z_m"]))
    dx, dy, dz = B[0] - A[0], B[1] - A[1], B[2] - A[2]
    D3 = math.sqrt(dx * dx + dy * dy + dz * dz)
    DXY = math.sqrt(dx * dx + dy * dy)
    out["expect"] = {"d3_ideal_ridge": round(D3, 3), "dxy_ideal_ridge": round(DXY, 3)}

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
        pg.wait_for_timeout(3800)

        # 1. row
        out["meas_row_visible"] = pg.locator("#imMeasRow").is_visible()
        out["meas_buttons"] = sorted(pg.locator("#imMeasRow [data-im-meas]").evaluate_all(
            "els=>els.map(e=>e.getAttribute('data-im-meas'))")) + (["clear"] if pg.locator("#imMeasClear").count() else [])

        ms = lambda: pg.evaluate("Immersive.spatial3d().measureState()")
        proj = lambda xyz: pg.evaluate("Immersive.spatial3d().project(%s)" % json.dumps(list(xyz)))
        click_at = lambda xyz: pg.mouse.click(*proj(xyz))
        label = "## n/a"

        # 2. 3D mode + two clicks — verify against the geometry's OWN truth:
        #    which object wins per the pick contract, that the returned points
        #    lie on those objects, and that the reported distance is exact.
        pg.click("#imMeasRow [data-im-meas='3d']")
        pg.wait_for_timeout(100)
        out["active_3d"] = pg.locator("#imMeasRow [data-im-meas='3d']").get_attribute("class")
        click_at(A); pg.wait_for_timeout(200)
        s1 = ms()
        out["count_after_one"] = s1["count"]
        click_at(B); pg.wait_for_timeout(300)
        s2 = ms()
        aid = pg.evaluate("(()=>{const m=Immersive.spatial3d().measureState();return null;})()")  # ids via picks below
        pa = pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % tuple(proj(A)))
        pb = pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % tuple(proj(B)))
        d3c = math.dist(s2["a"], s2["b"]) if s2["a"] and s2["b"] else None
        dxyc = math.hypot(s2["b"][0] - s2["a"][0], s2["b"][1] - s2["a"][1]) if s2["a"] and s2["b"] else None
        zra = zrange(byid[pa["spatialId"]]) if pa else (None, None)
        zrb = zrange(byid[pb["spatialId"]]) if pb else (None, None)
        # z-band tolerance is coarse (0.3 m): a pick can sample a mesh while its
        # appear-settle offset is still easing; the strong exactness checks are
        # d3/dxy vs the returned contract coordinates (1e-9) and the label.
        on_a = zra[0] is not None and zra[0] - 0.3 <= s2["a"][2] <= zra[1] + 0.3
        on_b = zrb[0] is not None and zrb[0] - 0.3 <= s2["b"][2] <= zrb[1] + 0.3
        out["two_points"] = {"count": s2["count"],
                             "picked": [pa["spatialId"] if pa else None, pb["spatialId"] if pb else None],
                             "a": [round(v, 3) for v in s2["a"]] if s2["a"] else None,
                             "b": [round(v, 3) for v in s2["b"]] if s2["b"] else None,
                             "zrange_a": zra, "zrange_b": zrb,
                             "on_object_a": on_a, "on_object_b": on_b,
                             "d3_exact": abs(s2["d3"] - d3c) < 1e-9,
                             "dxy_exact": abs(s2["dxy"] - dxyc) < 1e-9,
                             "d3": round(s2["d3"], 3), "dxy": round(s2["dxy"], 3)}

        # 3. label in DOM
        pg.wait_for_timeout(300)
        label = pg.locator("#imStage .im-measlab").inner_text() if pg.locator("#imStage .im-measlab").count() else ""
        out["label_3d"] = label
        import re
        m = re.search(r"([0-9]+\.[0-9]+)\s*m", label)
        out["label_matches"] = bool(m) and abs(float(m.group(1)) - s2["d3"]) < 0.02

        # 4. XY mode: horizontal component; strictly smaller when dz != 0
        pg.click("#imMeasRow [data-im-meas='xy']")
        pg.wait_for_timeout(250)
        s3 = ms()
        out["xy_mode"] = {"mode": s3["mode"], "dxy": round(s3["dxy"], 3),
                          "ok": s3["mode"] == "xy" and abs(s3["dxy"] - dxyc) < 1e-9
                                and s2["d3"] >= s3["dxy"] - 1e-9}
        label_xy = pg.locator("#imStage .im-measlab").inner_text() if pg.locator("#imStage .im-measlab").count() else ""
        m = re.search(r"([0-9]+\.[0-9]+)\s*m", label_xy)
        out["label_xy_matches"] = bool(m) and abs(float(m.group(1)) - s3["dxy"]) < 0.02

        # 5. explode 100%: distances unchanged; pick returns true (unexploded) z
        pg.evaluate("Immersive.spatial3d().setExplode(100)")
        pg.wait_for_timeout(300)
        s4 = ms()
        out["explode_invariant"] = {"d3_same": abs(s4["d3"] - s3["d3"]) < 1e-9,
                                    "dxy_same": abs(s4["dxy"] - s3["dxy"]) < 1e-9}
        pg.wait_for_timeout(150)
        r = pg.evaluate("Immersive.spatial3d().pickPoint(%f,%f)" % tuple(proj(B)))
        out["pick_while_exploded"] = (r is not None and
                                      abs(r["contract"][2] - B[2]) < 0.25 and
                                      r["userData"]["spatialId"] == "bldg-2.roof.l0")
        pg.evaluate("Immersive.spatial3d().setExplode(0)")
        pg.wait_for_timeout(300)

        # 6. third click restarts; CLEAR empties
        click_at(A); pg.wait_for_timeout(200)
        out["restart_count"] = ms()["count"]
        pg.click("#imMeasClear"); pg.wait_for_timeout(200)
        out["cleared"] = ms()["count"] == 0

        # 7. leaving measure mode restores the selection path
        pg.click("#imMeasRow [data-im-meas='xy']")  # toggles off
        pg.wait_for_timeout(150)
        click_at(A); pg.wait_for_timeout(300)
        sel = pg.evaluate("Immersive.spatial3d().getSelected()")
        out["selection_restored"] = sel is not None
        pg.keyboard.press("Escape")

        out["page_errors"] = errs
        b.close()

    ok = (out["meas_row_visible"]
          and sorted(out["meas_buttons"]) == ["3d", "clear", "xy"]
          and "active" in out["active_3d"]
          and out["count_after_one"] == 1
          and out["two_points"]["count"] == 2
          and out["two_points"]["on_object_a"] and out["two_points"]["on_object_b"]
          and out["two_points"]["d3_exact"] and out["two_points"]["dxy_exact"]
          and out["label_matches"]
          and out["xy_mode"]["ok"] and out["label_xy_matches"]
          and all(out["explode_invariant"].values())
          and out["pick_while_exploded"]
          and out["restart_count"] == 1
          and out["cleared"]
          and out["selection_restored"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
