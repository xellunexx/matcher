# -*- coding: utf-8 -*-
"""Step 5 verification v2: phase-ladder BUILD + no fake time + honest occlusion picking.
 - BUILD start at cursor 0: nothing pickable (no existing stock in this project)
 - stepping after build-start advances the phase counter and surfaces foundation
 - final EXPLORE: only surface objects pickable (roof/facade/site/nets), slabs occluded
 - WALLS filter chip reveals wall_ext as pickable; SLABS chip reveals slabs
 - no days/simulated anywhere; done overlay counts phases
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
    est_id = http("/api/estimation/history")["history"][0]["id"]
    sp = http("/api/estimation/spatial?id=" + est_id)
    sv2 = sp.get("scene_v2") or {}
    objs = sv2.get("objects") or []
    out["est_id"] = est_id
    out["objects"] = len(objs)
    out["buildings"] = [b.get("id") for b in (sv2.get("buildings") or [])]
    roofs = {o["id"] for o in objs if "roof" in o["id"]}
    walls = {o["id"] for o in objs if ".wall-" in o["id"]}
    slabs = {o["id"] for o in objs if ".slab." in o["id"]}
    siteish = {o["id"] for o in objs if o.get("phase") in ("site", "mep")}

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
        pg.wait_for_timeout(3000)

        out["explore_pickable"] = sorted(pick_ids(pg))

        # BUILD from the start overlay
        pg.evaluate("[...document.querySelectorAll('.im-modeb')].find(b=>b.getAttribute('data-im-mode')==='BUILD')?.click()")
        pg.wait_for_timeout(400)
        pg.evaluate("document.getElementById('imBuild')?.click()")
        pg.wait_for_timeout(500)
        pg.evaluate("document.getElementById('imToStart')?.click()")
        pg.wait_for_timeout(700)
        out["pick_at_0"] = sorted(pick_ids(pg))
        out["day_at_0"] = pg.locator("#imDay").inner_text() if pg.locator("#imDay").count() else None

        for _ in range(80):
            txt = pg.locator("#imDay").inner_text() if pg.locator("#imDay").count() else ""
            mm = txt.split("от")[0].strip().split()[-1] if "от" in txt else "0"
            try:
                ph_i = int(mm)
            except Exception:
                ph_i = 0
            if ph_i >= 3:  # structure phase reached — foundations long done
                break
            pg.evaluate("document.getElementById('imStepFwd')?.click()")
            pg.wait_for_timeout(140)
        out["day_mid"] = pg.locator("#imDay").inner_text() if pg.locator("#imDay").count() else None
        out["pick_mid"] = sorted(pick_ids(pg))

        pg.evaluate("const r=document.getElementById('imRange'); r.value=1000; r.dispatchEvent(new Event('input',{bubbles:true}));")
        pg.wait_for_timeout(1800)
        out["day_end"] = pg.locator("#imDay").inner_text() if pg.locator("#imDay").count() else None
        out["pick_end"] = sorted(pick_ids(pg))
        out["done_overlay"] = pg.locator("#imDone:not([hidden])").count()
        out["done_text"] = pg.locator("#imDoneCard").inner_text() if pg.locator("#imDoneCard").count() else None

        # filters reveal covered categories
        pg.evaluate("document.getElementById('imDoneClose')?.click()")
        pg.evaluate("[...document.querySelectorAll('#imFilterChips [data-imf-cat]')].find(b=>b.getAttribute('data-imf-cat')==='SLABS')?.click()")
        pg.wait_for_timeout(400)
        out["pick_slabs_filter"] = sorted(pick_ids(pg))
        pg.evaluate("[...document.querySelectorAll('#imFilterChips [data-imf-cat]')].find(b=>b.getAttribute('data-imf-cat')==='WALLS')?.click()")
        pg.wait_for_timeout(400)
        out["pick_walls_filter"] = sorted(pick_ids(pg))
        pg.evaluate("[...document.querySelectorAll('#imFilterChips [data-imf-cat]')].find(b=>b.getAttribute('data-imf-cat')==='ALL')?.click()")
        pg.wait_for_timeout(300)

        body = pg.evaluate("document.body.innerText")
        out["fake_time_markers"] = [m for m in ["(simulated)", "Day ", "Ден ", "Tage (sim", "jours (sim"] if m in body]
        out["hud_date_rows"] = pg.evaluate("!!document.getElementById('imInit')||!!document.getElementById('imDoneExp')")
        out["errors"] = list(errs)
        b.close()

    p0, pm, pe = set(out["pick_at_0"]), set(out["pick_mid"]), set(out["pick_end"])
    checks = [
        ("scene: two buildings, 28 objects", out["objects"] == 28 and len(out["buildings"]) == 2),
        ("phase 0 empty (no existing stock)", len(p0) == 0),
        ("steps advance into structure phase", out["day_mid"] and not out["day_mid"].startswith("Фаза 0 от 9") and not out["day_mid"].startswith("Фаза 1 ")),
        ("mid counter advances past 0", out["day_mid"] and not out["day_mid"].startswith("Фаза 0")),
        ("end: roofs and site/nets pickable", bool(pe & roofs) and bool(pe & siteish)),
        ("end: occluded slabs NOT surface-pickable", not (pe & slabs)),
        ("end: counter full", bool(out["day_end"] and (out["day_end"].startswith("Фаза 9") or "9 от 9" in out["day_end"]))),
        ("done overlay with phases text", out["done_overlay"] == 1 and out["done_text"] and ("фаз" in out["done_text"].lower() or "phase" in out["done_text"].lower()) and "simul" not in out["done_text"].lower()),
        ("WALLS filter reveals walls", len(set(out["pick_walls_filter"]) & walls) >= 1),
        ("SLABS filter reveals slabs", len(set(out["pick_slabs_filter"]) & slabs) >= 2),
        ("no fake-time markers", not out["fake_time_markers"]),
        ("no HUD date rows", out["hud_date_rows"] is False),
        ("no JS errors", not out["errors"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
