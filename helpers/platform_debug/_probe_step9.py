# -*- coding: utf-8 -*-
"""Step 9 verification: view-by modes (Material/Evidence/Phase) + §21 evidence line."""
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
            est_id, sp = h["id"], probe
            break
    sv2 = sp.get("scene_v2") or {}
    objs = sv2.get("objects") or []
    ks_of = {}
    for o in objs:
        ks_of[o["id"]] = o.get("knowledge_state")
    out["est_id"] = est_id
    out["states"] = sorted(set(ks_of.values()))

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

        out["view_row_visible"] = pg.locator("#imViewRow").is_visible() if pg.locator("#imViewRow").count() else False
        out["view_buttons"] = pg.locator("#imViewBtns [data-im-view]").all_inner_texts()

        def display_state():
            return pg.evaluate("(()=>{const h=Immersive.spatial3d();return h&&h.describeDisplay?h.describeDisplay():null;})()") or []

        base = {(e["id"]): e for e in display_state()}
        out["mode_default"] = next(iter(base.values()), {}).get("mode")

        # Evidence mode
        pg.click("#imViewBtns [data-im-view='evidence']")
        pg.wait_for_timeout(600)
        ev = {e["id"]: e for e in display_state()}
        out["mode_after_evidence"] = next(iter(ev.values()), {}).get("mode")
        out["legend_evidence"] = pg.locator("#imViewLegend").inner_text() if pg.locator("#imViewLegend").count() else None
        obs_ok, der_ok, inf_ok, unk_ok = True, True, True, True
        for e in ev.values():
            ks = e["knowledgeState"]
            if ks == "OBSERVED" and (e["wireframe"] or abs(e["modeK"] - 1) > 1e-6): obs_ok = False
            if ks == "DERIVED" and (e["wireframe"] or abs(e["modeK"] - 0.85) > 1e-6): der_ok = False
            if ks == "INFERRED" and (not e["wireframe"] or e["modeK"] >= 0.5): inf_ok = False
            if ks == "UNKNOWN" and not e["wireframe"]: unk_ok = False
        out["evidence_rules"] = {"observed": obs_ok, "derived": der_ok, "inferred": inf_ok, "unknown": unk_ok}
        # inferred objects never upgraded by having a price: check their colors are base (not boosted)
        out["inferred_keep_base_color"] = all(e["color"] == e["baseColor"] for e in ev.values())

        # Phase mode: same phase → same color; different phases → different color
        pg.click("#imViewBtns [data-im-view='phase']")
        pg.wait_for_timeout(600)
        ph = {e["id"]: e for e in display_state()}
        by_phase = {}
        for e in ph.values():
            if e["color"]:
                by_phase.setdefault(e["phase"], set()).add(e["color"])
        out["phase_color_map"] = {k: sorted(v)[:2] for k, v in sorted(by_phase.items())}
        out["legend_phase"] = pg.locator("#imViewLegend").inner_text() if pg.locator("#imViewLegend").count() else None

        # picking still works in evidence mode (wireframe objects stay raycastable)
        pg.click("#imViewBtns [data-im-view='evidence']")
        pg.wait_for_timeout(400)
        pmap = pg.evaluate("""(()=>{const h=Immersive.spatial3d();const box=document.querySelector('#imStage canvas').getBoundingClientRect();
          const s=new Set();for(let iy=0; iy<18; iy++)for(let ix=0; ix<26; ix++){
            const p=h.pick(box.x+box.width*(ix+0.5)/26, box.y+box.height*(iy+0.5)/18);
            if(p&&p.spatialId)s.add(p.spatialId);} return [...s];})()""")
        out["evidence_pickable_n"] = len(pmap or [])

        # back to material: colors restored
        pg.click("#imViewBtns [data-im-view='material']")
        pg.wait_for_timeout(600)
        mat = {e["id"]: e for e in display_state()}
        out["material_restores"] = all(
            (not base[i]) or (m["color"] == base[i]["color"] and not m["wireframe"])
            for i, m in mat.items())
        out["errors"] = list(errs)
        b.close()

    checks = [
        ("view row visible", out["view_row_visible"] is True),
        ("4 view buttons (§4+§5: material/evidence/phase/cost)", len(out["view_buttons"]) == 4),
        ("default mode material", out["mode_default"] == "material"),
        ("applied mode evidence", out["mode_after_evidence"] == "evidence"),
        ("OBSERVED solid", out["evidence_rules"]["observed"]),
        ("DERIVED dimmed", out["evidence_rules"]["derived"]),
        ("INFERRED translucent + wireframe (§21 evidence view)", out["evidence_rules"]["inferred"]),
        ("UNKNOWN never upgraded", out["evidence_rules"]["unknown"]),
        ("inferred objects keep base color (price never promotes certainty)", out["inferred_keep_base_color"] is True),
        ("evidence legend present", bool(out["legend_evidence"])),
        ("phase mode: single color per phase", all(len(v) == 1 for v in out["phase_color_map"].values())),
        ("phase mode: ≥2 distinct ladder colors", len({c for v in out["phase_color_map"].values() for c in v}) >= 2),
        ("phase legend lists used phases", bool(out["legend_phase"])),
        ("picking works in evidence mode", out["evidence_pickable_n"] >= 2),
        ("material restores canonical colors", out["material_restores"] is True),
        ("no JS errors", not out["errors"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
