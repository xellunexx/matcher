# -*- coding: utf-8 -*-
"""Step 11 verification: camera tools (plan §10).

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. #imCamRow visible with 6 tools
 2. dbl-click object -> smooth fit: controls.target lands on that object's box
    center (contract coords converted x,z,-y) and the object projects at the
    canvas center; animation settles (animating false)
 3. dbl-click empty -> fit project: target == scene bounds center
 4. fit-project + fit-selected buttons move the camera to the same boxes
 5. presets top/front/side/perspective produce the expected dominant axes
    (top: +Y dominates; front: +Z dominates; side: +X dominates; perspective:
    matches none of the axis views)
 6. picking still works after camera moves (orbit/selection preserved)
 7. no page errors
"""
import json, math, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def cv(x, y, z):  # contract (x_east, y_north, z_up) -> three (x, z_up, -y_north)
    return (x, z, -y)

def box3(o):
    """Approximate 3D box center of a scene_v2 object, in contract coords."""
    g = o.get("geometry") or {}
    t = g.get("type")
    if t == "extrude":
        poly = g.get("polygon_m") or []
        xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
        z0 = float(g.get("z0_m") or 0); z1 = float(g.get("z1_m") or z0)
        return ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (z0+z1)/2)
    if t == "box":
        c = g.get("center_m")
        return tuple(float(v) for v in c) if c else None
    if t == "cylinder":
        c = g.get("center_m")
        return tuple(float(v) for v in c) if c else None
    return None

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
    byid = {o["id"]: o for o in objs}
    out["est_id"] = est_id

    # target object: a visible roof/box object of bldg-2 (foundation is buried
    # below terrain — step-8 finding — so it must not be the click target)
    TARGET = next((o["id"] for o in objs
                   if "bldg-2" in o["id"] and "roof" in o["id"] and box3(o)),
                  None) or next((o["id"] for o in objs
                                 if "bldg-2" in o["id"] and "wall" in o["id"] and box3(o)), None)
    assert TARGET, "no visible target object in pack"
    tb = box3(byid[TARGET])
    assert tb, "no box for target object"

    bm = sv2.get("bounds_m")
    if isinstance(bm, dict):
        bmin, bmax = bm.get("min"), bm.get("max")
    else:
        bmin, bmax = bm[0], bm[1]
    bcen = tuple((bmin[i] + bmax[i]) / 2 for i in range(3))

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

        # 1. tools row
        btns = pg.locator("#imCamBtns [data-im-cam]")
        out["cam_row_visible"] = pg.locator("#imCamRow").is_visible()
        out["cam_buttons"] = sorted(btns.evaluate_all("els=>els.map(e=>e.getAttribute('data-im-cam'))"))

        cam = lambda: pg.evaluate("Immersive.spatial3d().camState()")
        proj = lambda xyz: pg.evaluate("Immersive.spatial3d().project(%s)" % json.dumps(list(xyz)))
        settle = lambda: pg.wait_for_timeout(900)

        def near(a, b_, tol):
            return all(abs(a[i]-b_[i]) <= tol for i in range(3))

        def canvas_center():
            r = pg.evaluate("Immersive.spatial3d().canvas.getBoundingClientRect()")
            return (r["left"] + r["width"]/2, r["top"] + r["height"]/2, r["width"], r["height"])

        def center_err(xyz):
            """pixel distance of a world point from canvas center, normalized by half-size"""
            p = proj(xyz); cc = canvas_center()
            if not p: return None
            return math.hypot(p[0]-cc[0], p[1]-cc[1]) / max(1.0, min(cc[2], cc[3])/2)

        # 2. dbl-click an object -> fit/focus THE PICKED object (semantic pick
        # priority decides what wins under the cursor — e.g. an MEP shaft in
        # front of a wall center; the probe follows the pick contract)
        anchor = proj(tb)
        assert anchor, "target not on camera before dblclick"
        picked = pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % (anchor[0], anchor[1]))
        assert picked and picked.get("spatialId"), "anchor pick returned nothing"
        pid = picked["spatialId"]
        pb = box3(byid[pid])
        pg.mouse.dblclick(anchor[0], anchor[1])
        settle()
        st = cam()
        out["target_object"] = TARGET
        out["picked_object"] = pid
        out["dblclick_obj"] = {
            "animating_done": not st["animating"],
            "target_ok": pb is not None and near(st["target"], cv(*pb), tol=2.5),
            "centered": pb is not None and center_err(pb) is not None and center_err(pb) < 0.35,
            "selected": pg.evaluate("Immersive.spatial3d().getSelected()") == pid,
        }

        # 4a. fit-selected while the dblclick selection is still active
        pg.click("#imCamBtns [data-im-cam='fit-selected']")
        settle()
        out["btn_fit_selected"] = near(cam()["target"], cv(*pb), tol=2.5)

        # 3. back at project framing first (the corner pixel is only "sky" from
        #    far out; close-ups legitimately intersect site/context meshes there)
                # 4b. fit-project button -> same box as dblclick-empty
        pg.click("#imCamBtns [data-im-cam='fit-project']")
        settle()
        out["btn_fit_project"] = near(cam()["target"], cv(*bcen), tol=3.0)


        # 3. dbl-click empty sky (top-left corner) -> fit project
        #    (also clears selection — later steps must not depend on it)
        pg.mouse.dblclick(pg.evaluate("Immersive.spatial3d().canvas.getBoundingClientRect()")["left"] + 6,
                          pg.evaluate("Immersive.spatial3d().canvas.getBoundingClientRect()")["top"] + 6)
        settle()
        st = cam()
        out["dblclick_empty"] = {
            "target_ok": near(st["target"], cv(*bcen), tol=3.0),
            "centered": center_err(bcen) is not None and center_err(bcen) < 0.3,
        }

        # 5. presets: dominant-axis geometry
        def axis_stats():
            s = cam(); p, t = s["pos"], s["target"]
            d = [abs(p[i]-t[i]) for i in range(3)]
            tot = max(1e-9, sum(d))
            return [v/tot for v in d], [p[i]-t[i] for i in range(3)]
        pres = {}
        for name, dom in (("top", 1), ("front", 2), ("side", 0)):
            pg.click("#imCamBtns [data-im-cam='%s']" % name)
            settle()
            frac, raw = axis_stats()
            pres[name] = bool(frac[dom] > 0.75 and frac[dom] == max(frac)
                              and raw[dom] > 0)
        pg.click("#imCamBtns [data-im-cam='perspective']")
        settle()
        frac, _ = axis_stats()
        pres["perspective"] = all(f < 0.85 for f in frac)  # a 3/4 view, not an axis
        out["presets"] = pres

        # 6. picking alive after all the flying (assert against the pick
        # contract's own winner, not an assumed id)
        anchor = proj(tb)
        assert anchor
        want = pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % (anchor[0], anchor[1]))
        pg.mouse.click(anchor[0], anchor[1])
        pg.wait_for_timeout(250)
        out["pick_after_camera"] = bool(want) and pg.evaluate("Immersive.spatial3d().getSelected()") == want.get("spatialId")
        pg.keyboard.press("Escape")  # close any inspector drawer
        out["page_errors"] = errs
        b.close()

    ok = (out["cam_row_visible"]
          and out["cam_buttons"] == sorted(["fit-project", "fit-selected", "top", "front", "side", "perspective"])
          and all(out["dblclick_obj"].values())
          and all(out["dblclick_empty"].values())
          and out["btn_fit_selected"] and out["btn_fit_project"]
          and all(out["presets"].values())
          and out["pick_after_camera"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
