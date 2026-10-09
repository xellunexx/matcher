# -*- coding: utf-8 -*-
"""Step 3 verification probe: visibility/filters/building selector.
Real EST project on :8123 (scene_v2, roof object known pickable from step 2).
 - filter row renders: 11 category chips + building chips from scene_v2.buildings
 - FOUNDATIONS filter -> previously hoverable roof point stops yielding a tooltip
 - ALL restores it
 - building chip (bldg-1) keeps roof hoverable (member), All-buildings resets
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = hist["history"][0]["id"]
    sp = http("/api/estimation/spatial?id=" + est_id)
    sv2 = sp.get("scene_v2") or {}
    out["est_id"] = est_id
    out["buildings_in_scene"] = [b.get("id") for b in (sv2.get("buildings") or [])]
    out["objects"] = len(sv2.get("objects") or [])

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

        row = pg.locator("#imFilterRow")
        out["filter_row_visible"] = row.is_visible() if row.count() else False
        out["cat_chips"] = pg.locator("#imFilterChips [data-imf-cat]").count()
        out["bld_chips"] = pg.locator("#imBldChips [data-imf-bld]").count()
        out["bld_chip_labels"] = pg.locator("#imBldChips [data-imf-bld]").all_inner_texts()

        def tooltip_state(x, y):
            pg.mouse.move(x, y)
            pg.wait_for_timeout(110)
            t = pg.locator(".im-s3dtip")
            if t.count() and t.is_visible():
                tx = t.inner_text().strip()
                return tx if tx else None
            return None

        # find a hoverable point (roof object seen in step 2 grid scans)
        box = pg.locator("#imStage canvas").first.bounding_box()
        hit = None
        xs = [box["x"] + box["width"] * (i + 0.5) / 12 for i in range(12)]
        ys = [box["y"] + box["height"] * (i + 0.5) / 8 for i in range(8)]
        for yy in ys:
            if hit: break
            for xx in xs:
                t = tooltip_state(xx, yy)
                if t:
                    hit = {"x": xx, "y": yy, "tip": t}
                    break
        out["initial_hit"] = hit

        if hit:
            # FOUNDATIONS filter -> hit point must go silent
            pg.click("#imFilterChips [data-imf-cat='FOUNDATIONS']")
            pg.wait_for_timeout(350)
            after_found = tooltip_state(hit["x"], hit["y"])
            # ALL restores
            pg.click("#imFilterChips [data-imf-cat='ALL']")
            pg.wait_for_timeout(350)
            after_all = tooltip_state(hit["x"], hit["y"])
            # building chip keeps member hoverable
            blds = pg.locator("#imBldChips [data-imf-bld]")
            by_bld = None
            if blds.count() > 1:
                blds.nth(1).click()
                pg.wait_for_timeout(350)
                by_bld = tooltip_state(hit["x"], hit["y"])
                blds.nth(0).click()
                pg.wait_for_timeout(300)
            out["after_foundations"] = after_found
            out["after_all"] = after_all
            out["after_bldg_chip"] = by_bld
        out["errors"] = list(errs)
        b.close()

    checks = [
        ("scene has objects", out["objects"] > 0),
        ("filter row visible", out["filter_row_visible"] is True),
        ("11 category chips", out["cat_chips"] == 11),
        ("building chips = 1 + All", out["bld_chips"] == len(out["buildings_in_scene"]) + 1),
        ("initial hover hit exists", out.get("initial_hit") is not None),
        ("FOUNDATIONS hides the roof hit", out.get("after_foundations") is None),
        ("ALL restores hover", bool(out.get("after_all"))),
        ("building chip keeps member pickable", (out.get("after_bldg_chip") is not None) if out["buildings_in_scene"] else True),
        ("no JS errors", not out["errors"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
