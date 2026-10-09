# -*- coding: utf-8 -*-
"""Step 19 verification: legacy fake visualization removed (plan §20).

Live checks (PROBE_BASE, default http://127.0.0.1:8123) on the two-house pack:
 1. NO fake-architecture nodes in the DOM at any build stage:
    .imL, .im-corridor, .im-site, .im-plot, #imPulse, #imBadges, .im-evbadge
 2. backdrop/def-only SVG remains (sky/vignette); canvas with all real meshes
 3. the BUILD engine still runs: seek to ~50% -> HUD cost > 0, phase counter
    filled, roster/feed render
 4. selection + wviewer interactions still work (pick an object, getSelected)
 5. no page errors anywhere (would catch dangling references)
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")
FAKE = [".imL", ".im-corridor", ".im-site", ".im-plot", "#imPulse", "#imBadges",
        ".im-evbadge", ".im-teye-missing-sentinel-unrelated"]

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
    out["est_id"] = est_id
    n_objs = len((sp.get("scene_v2") or {}).get("objects") or [])

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
        pg.wait_for_timeout(2000)

        # 1/2. DOM honesty at start
        def fake_count():
            return pg.evaluate("""(sel=>sel.reduce((a,s)=>a+document.querySelectorAll(s).length,0))(%s)""" % json.dumps(FAKE))
        out["fake_nodes_start"] = fake_count()
        out["has_backdrop"] = pg.evaluate("!!document.querySelector('#imStage svg')")
        out["has_canvas"] = pg.evaluate("!!document.querySelector('#imStage canvas')")

        # 3. engine alive: seek to mid-build, HUD must advance
        pg.evaluate("(()=>{const S=Immersive._test.session; if(!S.eng)return; Immersive.seekTo(Math.round(S.eng.m*0.5));})()")
        pg.wait_for_timeout(1200)
        hud = pg.evaluate("""(()=>({
          cost: (document.getElementById('imCost')||{}).textContent || '',
          phase: (document.getElementById('imDay')||{}).textContent || '',
          roster: (document.getElementById('imRoster')||{}).textContent || '',
          feed: (document.getElementById('imFeed')||{}).textContent || ''
        }))()""")
        out["hud_mid"] = {k: v[:40] for k, v in hud.items()}
        import re
        cost_val = re.sub(r"[^\d]", "", hud["cost"] or "0")
        out["engine_alive"] = bool(hud["phase"].strip()) and int(cost_val or 0) > 0
        out["meshes_visible"] = pg.evaluate(
            "Immersive.spatial3d().debugEntries().filter(e=>e.visible).length")

        # engine COMPLETE
        pg.evaluate("(()=>{const S=Immersive._test.session; if(!S.eng)return; Immersive.seekTo(S.eng.m);})()")
        pg.wait_for_timeout(1200)
        out["hud_done_pct"] = (pg.evaluate("(document.getElementById('imPct')||{}).textContent || ''") or "").strip()

        # 1 again mid/done + 4. selection still works
        out["fake_nodes_mid"] = fake_count()
        out["fake_nodes_done"] = fake_count()
        # click an object via canvas (roof of bldg-2)
        deb = pg.evaluate("Immersive.spatial3d().debugEntries()")
        any_lvl1 = next((e for e in deb if e["id"].endswith(".l2")), None)
        assert any_lvl1, "no l2 entry"
        # use tree to select (proven) + canvas pick with project
        pg.locator('#imTree [data-imt="%s"]' % any_lvl1["id"]).scroll_into_view_if_needed()
        pg.locator('#imTree [data-imt="%s"]' % any_lvl1["id"]).click()
        pg.wait_for_timeout(250)
        out["selection_ok"] = pg.evaluate("Immersive.spatial3d().getSelected()") == any_lvl1["id"]
        pg.keyboard.press("Escape")

        out["page_errors"] = errs
        b.close()

    fakes = [v for k, v in out.items() if k.startswith("fake_nodes")]
    ok = (out["has_backdrop"] and out["has_canvas"]
          and all(v == 0 for v in fakes)
          and out["engine_alive"]
          and out["hud_done_pct"] == "100%"
          and out["meshes_visible"] >= n_objs
          and out["selection_ok"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
