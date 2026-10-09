# -*- coding: utf-8 -*-
"""Step 4 (§2 inspector + §4a quantity semantics) live verification on :8123."""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

out = {"ok": True}
try:
    est_id = http("/api/estimation/history")["history"][0]["id"]
    sp = http("/api/estimation/spatial?id=" + est_id)
    recon = sp.get("quantity_reconciliation") or {}
    ca = ((sp.get("scene_v2") or {}).get("cost_allocation")) or {}
    out["recon"] = {k: (len(v) if isinstance(v, list) else v) for k, v in recon.items()}
    out["row_categories"] = len(ca.get("row_categories") or {})

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

        # 4a.A: MATERIALS lens line must not contain unit pseudo-totals
        pg.click("[data-im-lens='MATERIALS']") if pg.locator("[data-im-lens='MATERIALS']").count() else pg.evaluate("""
          [...document.querySelectorAll('button')].find(b=>/материал|material/i.test(b.textContent))?.click()
        """)
        pg.wait_for_timeout(400)
        lens_text = pg.locator("#imLensInfo").inner_text() if pg.locator("#imLensInfo").count() else ""
        out["materials_lens_text"] = lens_text

        # 4a.C/D + §2: open the project aggregation drawer (openContainer)
        pg.evaluate("window.ImmersiveInspector && ImmersiveInspector.openContainer(null)")
        pg.wait_for_timeout(600)
        d = pg.locator("#drawer")
        out["project_agg"] = {
            "open": d.get_attribute("aria-hidden") == "false",
            "has_groups": pg.locator("#drawer .kv-mini-row").count(),
            "has_diag_details": pg.locator("#drawer details.insp-diag").count(),
            "diag_label": (pg.locator("#drawer details.insp-diag summary").inner_text() if pg.locator("#drawer details.insp-diag").count() else None),
            "qty_sum_label_present": "Σ quantity" in (d.inner_text() or "") or "Σ количество" in (d.inner_text() or ""),
        }
        pg.keyboard.press("Escape")
        pg.evaluate("closeDrawer && closeDrawer()")
        pg.wait_for_timeout(300)

        # §2: click a 3D object — drawer shows the new sections + actions
        box = pg.locator("#imStage canvas").first.bounding_box()
        tip = pg.locator(".im-s3dtip")
        hit = None
        for iy in range(8):
            if hit: break
            for ix in range(12):
                x = box["x"] + box["width"] * (ix + 0.5) / 12
                y = box["y"] + box["height"] * (iy + 0.5) / 8
                pg.mouse.move(x, y)
                pg.wait_for_timeout(80)
                if tip.count() and tip.is_visible():
                    hit = {"x": x, "y": y}
                    break
        out["hit"] = hit
        if hit:
            pg.mouse.click(hit["x"], hit["y"])
            pg.wait_for_timeout(700)
            dtxt = d.inner_text() if d.count() else ""
            out["object_drawer"] = {
                "open": d.get_attribute("aria-hidden") == "false",
                "has_spatial_id": "bldg-" in dtxt,
                "has_cost_share": (("Cost share" in dtxt) or ("дял" in dtxt.lower())),
                "has_evidence_word": ("Evidence" in dtxt or "Доказателства" in dtxt),
                "actions": {k: pg.locator(f"#drawer [{k}]").count() for k in
                            ["data-spo-isolate", "data-spo-hide", "data-spo-onlybld", "data-spo-fit", "data-spo-clear"]},
            }
            # actions actually work: fit camera, isolate, reset
            pg.locator("#drawer [data-spo-fit]").click()
            pg.wait_for_timeout(300)
            pg.locator("#drawer [data-spo-isolate]").click()
            pg.wait_for_timeout(400)
            iso_result = pg.evaluate(f"""(()=>{{const h=Immersive.spatial3d();if(!h)return null;
              const box=document.querySelector('#imStage canvas').getBoundingClientRect();
              const ids=new Set();
              for(let iy=0; iy<8; iy++)for(let ix=0; ix<12; ix++){{
                const p=h.pick(box.x+box.width*(ix+0.5)/12, box.y+box.height*(iy+0.5)/8);
                if(p&&p.spatialId)ids.add(p.spatialId);}}
              return JSON.stringify({{sel:h.getSelected(), pickable:[...ids]}});}})()""")
            out["after_isolate"] = json.loads(iso_result) if iso_result else None
            pg.locator("#drawer [data-spo-clear]").click()
            pg.wait_for_timeout(400)
            rst = pg.evaluate(f"""(()=>{{const h=Immersive.spatial3d();if(!h)return null;
              const box=document.querySelector('#imStage canvas').getBoundingClientRect();
              const ids=new Set();
              for(let iy=0; iy<8; iy++)for(let ix=0; ix<12; ix++){{
                const p=h.pick(box.x+box.width*(ix+0.5)/12, box.y+box.height*(iy+0.5)/8);
                if(p&&p.spatialId)ids.add(p.spatialId);}}
              return JSON.stringify([...ids]);}})()""")
            out["after_reset"] = {"pickable_count": len(json.loads(rst)) if rst else 0}
        out["errors"] = list(errs)
        b.close()

    iso = out.get("after_isolate") or {}
    checks = [
        ("recon exposed (boq_rows_total>0)", out["recon"].get("boq_rows_total", 0) > 0),
        ("recon lists no MISSING rows payload", out["recon"].get("excluded_rows", 0) == 0),
        ("row_categories exposed", out["row_categories"] > 0),
        ("materials lens has no unit pseudo-totals", not any(t in (out["materials_lens_text"] or "") for t in ["m²", "m³", "м²", "м³", " kg", " кг"])),
        ("project agg drawer opens", out["project_agg"]["open"]),
        ("semantic quantity groups render", out["project_agg"]["has_groups"] > 0),
        ("diagnostic raw sums labelled + collapsed", out["project_agg"]["has_diag_details"] == 1 and bool(out["project_agg"]["diag_label"])),
        ("object drawer has spatial id", bool(out.get("object_drawer", {}).get("has_spatial_id"))),
        ("object drawer has cost share", bool(out.get("object_drawer", {}).get("has_cost_share"))),
        ("object drawer has evidence section", bool(out.get("object_drawer", {}).get("has_evidence_word"))),
        ("all 5 actions render", all(v == 1 for v in out.get("object_drawer", {}).get("actions", {}).values())),
        ("isolate: only the isolated object stays pickable", iso.get("pickable") and len(iso["pickable"]) == 1 and iso["pickable"][0] == iso.get("sel")),
        ("reset: full pick set restored", out.get("after_reset", {}).get("pickable_count", 0) > 1),
        ("no JS errors", not out["errors"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
