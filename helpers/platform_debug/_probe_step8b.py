# -*- coding: utf-8 -*-
"""Step 8 addendum v2: embedded components aimed via project(objectAnchorOf) —
deterministic targeting instead of grid sweeps. Hide-chains mirror the user flow:
category filter → click skin → Hide → target is outermost → click target."""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = None
    for h in hist["history"]:
        probe = http("/api/estimation/spatial?id=" + h["id"])
        sv2 = probe.get("scene_v2") or {}
        if len(sv2.get("buildings") or []) == 2:
            est_id = h["id"]
            sp = probe
            break
    sv2 = sp.get("scene_v2") or {}
    byid = {o["id"]: o for o in (sv2.get("objects") or [])}
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
        pg.evaluate(f"openEstimationRun({json.dumps(est_id)})")
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(2800)

        def set_filter(cat):
            if cat:
                pg.evaluate(f"[...document.querySelectorAll('#imFilterChips [data-imf-cat]')].find(b=>b.getAttribute('data-imf-cat')==='{cat}')?.click()")
                pg.wait_for_timeout(450)

        def aim_target(oid):
            """client pixel of the object's anchor, or None (off-camera)."""
            gjson = json.dumps(byid[oid]["geometry"])
            return pg.evaluate(
                "(async()=>{"
                "const R=await import('./spatial3d-recipes.js');"
                "const h=Immersive.spatial3d(); if(!h||!h.project)return null;"
                f"const p=R.objectAnchorOf({{geometry:{gjson}}});"
                "if(!p)return null;"
                "const q=h.project(p); return q||null;})()")

        def hide(ids):
            pg.evaluate(f"Immersive.spatial3d().setHidden({json.dumps(ids)}, true)")
            pg.wait_for_timeout(320)

        def unhide_all():
            pg.evaluate("Immersive.spatial3d().resetVisibility()")
            pg.wait_for_timeout(320)

        def click_check(oid, pt):
            pg.mouse.click(pt[0], pt[1])
            pg.wait_for_timeout(550)
            d = pg.locator("#drawer")
            txt = d.inner_text() if d.count() and d.get_attribute("aria-hidden") == "false" else ""
            pg.evaluate("closeDrawer && closeDrawer()")
            pg.wait_for_timeout(280)
            return oid in txt

        hitmap = {}
        for bk in ("bldg-1", "bldg-2"):
            # wall l1
            t = f"{bk}.wall-ext.l1"
            unhide_all(); set_filter("ALL"); pg.wait_for_timeout(200)
            set_filter("WALLS")
            hide([f"{bk}.finish.l0", f"{bk}.insulate.l0", f"{bk}.wall-ext.l2"])
            pt = aim_target(t)
            hitmap[t] = click_check(t, pt) if pt else False
            # mep box
            t = f"{bk}.mep.l0"
            set_filter("ALL"); unhide_all(); set_filter("MEP")
            hide([f"{bk}.finish.l0", f"{bk}.insulate.l0", f"{bk}.wall-ext.l1", f"{bk}.wall-ext.l2", f"{bk}.roof.l0", f"{bk}.slab.l2"])
            pt = aim_target(t)
            hitmap[t] = click_check(t, pt) if pt else False
        # site surface
        set_filter("ALL"); unhide_all(); set_filter("SITE")
        pt = aim_target("bldg-1.site.l0")
        hitmap["bldg-1.site.l0"] = click_check("bldg-1.site.l0", pt) if pt else False
        # buried pipes
        set_filter("ALL"); unhide_all(); set_filter("NETWORKS")
        for nid in ("site-1.net-1", "site-1.net-2"):
            pt = aim_target(nid)
            hitmap[nid] = click_check(nid, pt) if pt else False
        set_filter("ALL"); unhide_all()

        out["hit_ok"] = hitmap
        out["errors"] = list(errs)
        checks = [(f"{k} reachable + click-identifies", v) for k, v in hitmap.items()]
        checks.append(("no JS errors", not errs))
        out["checks"] = [{"name": n, "pass": bool(c)} for n, c in checks]
        out["ok"] = all(c for _, c in checks)
        b.close()
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
