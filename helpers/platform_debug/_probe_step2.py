# -*- coding: utf-8 -*-
"""Step 2 verification probe: true picking + selection + hover.
Drives a real EST project (scene_v2) on :8123 and exercises:
 - hover grid scan -> tooltip appears with label/cost/qty/state content
 - hover cursor becomes pointer over geometry
 - click -> inspector drawer opens for the picked spatial object
 - ESC -> selection cleared (no errors)
 - empty-space click -> no crash, selection cleared
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

out = {"ok": True}
try:
    est_id = http("/api/estimation/history")["history"][0]["id"]
    out["est_id"] = est_id
    sp = http("/api/estimation/spatial?id=" + est_id)
    out["objects"] = len((sp.get("scene_v2") or {}).get("objects") or [])

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(600)
        pg.evaluate("setMode('estimation')")
        pg.wait_for_timeout(300)
        pg.evaluate(f"openEstimationRun({json.dumps(est_id)})")
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(3000)

        canvas = pg.locator("#imStage canvas").first
        box = canvas.bounding_box()
        out["canvas_box"] = box

        # grid scan for a hover hit
        hit = None
        tip = pg.locator(".im-s3dtip")
        xs = [box["x"] + box["width"] * (i + 0.5) / 12 for i in range(12)]
        ys = [box["y"] + box["height"] * (i + 0.5) / 8 for i in range(8)]
        for y in ys:
            if hit: break
            for x in xs:
                pg.mouse.move(x, y)
                pg.wait_for_timeout(70)
                if tip.count() and not tip.get_attribute("hidden") and tip.is_visible():
                    hit = {"x": x, "y": y, "text": tip.inner_text(),
                           "cursor": pg.evaluate("document.querySelector('#imStage canvas').style.cursor")}
                    break
        out["hover_hit"] = hit

        # click the hit point -> inspector drawer opens
        drawer_info = None
        if hit:
            pg.mouse.click(hit["x"], hit["y"])
            pg.wait_for_timeout(700)
            d = pg.locator("#drawer")
            drawer_info = {
                "aria_hidden": d.get_attribute("aria-hidden"),
                "text_sample": (d.inner_text() or "")[:240] if d.count() else None,
            }
        out["drawer_after_click"] = drawer_info

        # ESC clears selection (observable: no errors; tooltip hidden)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        out["after_esc"] = {"errors": [e for e in errs], "tip_hidden": (tip.get_attribute("hidden") is not None) if tip.count() else True}

        # empty-space click (top-left corner of canvas = sky)
        pg.mouse.click(box["x"] + 6, box["y"] + 6)
        pg.wait_for_timeout(400)
        out["errors_final"] = list(errs)
        b.close()

    checks = [
        ("scene objects present", out["objects"] > 0),
        ("hover found geometry", hit is not None),
        ("tooltip has content", bool(hit and hit["text"] and len(hit["text"].strip()) > 2)),
        ("cursor becomes pointer on hover", bool(hit and hit["cursor"] == "pointer")),
        ("drawer opens on click", bool(drawer_info and drawer_info["aria_hidden"] == "false")),
        ("drawer shows object content", bool(drawer_info and drawer_info["text_sample"] and len(drawer_info["text_sample"]) > 10)),
        ("no JS errors at all", not out["errors_final"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
