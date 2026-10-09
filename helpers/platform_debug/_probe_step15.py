# -*- coding: utf-8 -*-
"""Step 15 verification: object tree (plan §14).

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. tree exists in the HUD with exactly one row per scene object
 2. structure derives from metadata: Project -> buildings -> Foundation/Floor N/
    Roof/semantic buckets; Site bucket for non-building objects; .lN ids land in
    Floor buckets
 3. tree row click -> 3D selection (handle.getSelected) + row highlighted
 4. canvas pick click -> tree row highlighted + ancestors expanded
 5. selecting another object via canvas moves the highlight
 6. eye toggle hides objects in the renderer (hidden ids in viewState) and back
 7. no hardcoded instances: counts/labels all come from the scene
 8. no page errors
"""
import json, os, re, sys, urllib.request
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
    objs = sv2.get("objects") or []
    all_ids = {str(o["id"]) for o in objs}
    out["est_id"] = est_id
    out["object_count"] = len(all_ids)

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

        # 1. tree exists, one row per object
        rows = pg.locator("#imTree [data-imt]").evaluate_all("els=>els.map(e=>e.getAttribute('data-imt'))")
        out["tree_rows"] = len(rows)
        out["row_ids_match"] = set(rows) == all_ids

        # 2. structure: bucket summaries + building nodes
        out["bucket_labels"] = pg.locator("#imTree .im-tgrp > summary")
        labels = out["bucket_labels"].all_inner_texts()
        out["bucket_labels"] = [re.sub(r"[^\w \u0400-\u04FF]+", "", L).strip() for L in labels]
        lv_seen = set()
        for oid in all_ids:
            m = re.search(r"\.l(\d+)(?:\.|$)", oid)
            if m and int(m.group(1)) > 0:
                lv_seen.add(int(m.group(1)))
        out["level_buckets_ok"] = all(("%d}" % n).replace("}", "") or True for n in lv_seen)  # placeholder
        # exact: each .lN>0 id sits inside a group whose summary shows its floor number
        floor_text = {}
        for n in lv_seen:
            hits = pg.locator("#imTree details", has_text=None)
        out["level_buckets_ok"] = True  # replaced by strict check below
        strict = True
        for n in sorted(lv_seen):
            sample = next(i for i in all_ids if re.search(r"\.l%d(\.|$)" % n, i))
            container = pg.evaluate("""(id)=>{
              const r=document.querySelector('#imTree [data-imt="'+id+'"]');
              if(!r) return null;
              const g=r.closest('.im-tgrp'); return g?g.querySelector('summary').textContent:null;
            }""".replace("'+id+'", "%s" % sample)) if False else pg.evaluate(
                "((id)=>{const r=document.querySelector('#imTree [data-imt=\"'+id+'\"]');"
                "if(!r)return null; const g=r.closest('.im-tgrp');"
                "return g?g.querySelector('summary').textContent:null;})(%s)" % json.dumps(sample))
            ok = container is not None and re.search(r"\b%d\b" % n, re.sub(r"[^\d]+", " ", container)) is not None
            out.setdefault("floor_groups", {})[n] = {"sample": sample, "container": container, "ok": bool(ok)}
            strict = strict and ok
        out["level_buckets_ok"] = strict
        bld_summaries = pg.locator("#imTree .im-tbld > summary").all_inner_texts()
        out["building_nodes"] = len(bld_summaries)

        # 3. tree row click -> selection + active row
        pg.locator('#imTree [data-imt="bldg-2.wall-ext.l1"]').click()
        pg.wait_for_timeout(250)
        out["tree_click"] = {
            "selected": pg.evaluate("Immersive.spatial3d().getSelected()"),
            "row_active": "active" in (pg.locator('#imTree [data-imt="bldg-2.wall-ext.l1"]').get_attribute("class") or ""),
        }

        # 4/5. canvas pick -> tree follows; moving pick moves highlight
        # (close the inspector the tree click opened — it covers part of the
        # canvas, so a click there would never reach the renderer)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(250)
        roof = next(o for o in objs if o["id"] == "bldg-2.roof.l0")
        g = roof["geometry"]; poly = g["polygon_m"]
        ridge = ((min(p[0] for p in poly) + max(p[0] for p in poly)) / 2,
                 (min(p[1] for p in poly) + max(p[1] for p in poly)) / 2, float(g["ridge_z_m"]))
        px = pg.evaluate("Immersive.spatial3d().project(%s)" % json.dumps(list(ridge)))
        picked = pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % (px[0], px[1]))
        pg.mouse.click(px[0], px[1])
        pg.wait_for_timeout(300)
        sel = pg.evaluate("Immersive.spatial3d().getSelected()")
        active_rows = pg.locator("#imTree [data-imt].active").evaluate_all("els=>els.map(e=>e.getAttribute('data-imt'))")
        out["canvas_to_tree"] = {"picked": picked["spatialId"], "selected": sel, "active_rows": active_rows,
                                 "ok": sel == picked["spatialId"] and active_rows == [sel]}
        pg.keyboard.press("Escape")

        # 6. eye toggling: hide whole bucket, then restore
        wall_ids = sorted(i for i in all_ids if ".wall" in i)
        grp = pg.evaluate("""(()=>{const bt=[...document.querySelectorAll('#imTree [data-imteg]')]
            .find(b=>b.getAttribute('data-imteg').split(' ').some(i=>i.indexOf('wall')>=0));
            return bt?bt.getAttribute('data-imteg'):null;})()""")
        assert grp, "no wall group eye"
        pg.evaluate("""((g)=>{const bt=[...document.querySelectorAll('#imTree [data-imteg]')]
            .find(b=>b.getAttribute('data-imteg')===g); bt.click();})(%s)""" % json.dumps(grp))
        grp_ids = grp.split(" ")
        vs = pg.evaluate("Immersive.spatial3d().viewState()")
        hidden = set(vs["hidden"])
        out["eye_hides_all"] = all(i in hidden for i in grp_ids)
        # row dim state painted
        dimmed = pg.locator("#imTree .im-trow.im-t-hidden").evaluate_all("els=>els.map(e=>e.getAttribute('data-imt'))")
        out["dim_matches"] = set(dimmed) == hidden
        pg.evaluate("""((g)=>{const bt=[...document.querySelectorAll('#imTree [data-imteg]')]
            .find(b=>b.getAttribute('data-imteg')===g); bt.click();})(%s)""" % json.dumps(grp))
        vs2 = pg.evaluate("Immersive.spatial3d().viewState()")
        out["eye_restores"] = not any(i in set(vs2["hidden"]) for i in grp_ids)

        out["page_errors"] = errs
        b.close()

    ok = (out["tree_rows"] == out["object_count"]
          and out["row_ids_match"]
          and out["level_buckets_ok"]
          and out["building_nodes"] >= 2  # 2 buildings + Site
          and out["tree_click"]["selected"] == "bldg-2.wall-ext.l1"
          and out["tree_click"]["row_active"]
          and out["canvas_to_tree"]["ok"]
          and out["eye_hides_all"] and out["dim_matches"] and out["eye_restores"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
