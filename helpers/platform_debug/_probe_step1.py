# -*- coding: utf-8 -*-
"""Step 1 verification probe v4 (legacy fake-art removal)."""
import json, os, sys, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")
ROOT = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(ROOT, "data", "demo", "estimation_inbox")
TMP_ID = "EST20990101_000001"
TMP_PACK = os.path.join(INBOX, f"estimation_{TMP_ID}.json")
UNSUPRESSED = "[...document.querySelectorAll('#imStage [data-bucket]')].filter(e=>!e.closest('.im-s3dsup')).length"

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = hist["history"][0]["id"]
    out["est_id"] = est_id

    sp = http("/api/estimation/spatial?id=" + est_id)
    sv2 = sp.get("scene_v2") or {}
    objs = sv2.get("objects") or []
    out["scene_v2_objects"] = len(objs)
    ks = {}
    for o in objs:
        k = o.get("knowledge_state", "?"); ks[k] = ks.get(k, 0) + 1
    out["knowledge_states"] = ks
    out["buildings"] = len(sv2.get("buildings") or [])

    with open(os.path.join(INBOX, f"estimation_{est_id}.json"), encoding="utf-8") as f:
        pack = json.load(f)
    pack.pop("geometry_evidence", None)
    pack["tenderId"] = TMP_ID
    pack["name"] = "STEP1 no-geometry probe"
    with open(TMP_PACK, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False)

    sp2 = http("/api/estimation/spatial?id=" + TMP_ID)
    out["nogeo_scene_v2"] = bool(sp2.get("scene_v2"))
    out["nogeo_spatializable"] = bool((sp2.get("project") or {}).get("spatializable"))
    out["nogeo_representation"] = (sp2.get("project") or {}).get("representation")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(600)
        out["boot_errors"] = list(errs)
        pg.evaluate("setMode('estimation')")
        pg.wait_for_timeout(400)

        # B: real EST project (scene_v2 present)
        pg.evaluate(f"openEstimationRun({json.dumps(est_id)})")
        pg.wait_for_selector("#imRoot", timeout=30000)
        pg.wait_for_timeout(2500)
        out["scene_with_evidence"] = {
            "canvas": pg.locator("#imStage canvas").count(),
            "im_art": pg.locator(".im-art").count(),
            "im_ctx": pg.locator(".im-ctx").count(),
            "visible_bucket_layers": pg.evaluate(UNSUPRESSED),
            "noart_note": pg.locator(".im-noart").count(),
            "footnote": pg.locator("#imLegend").inner_text()[:160] if pg.locator("#imLegend").count() else None,
        }
        out["scene_errors"] = list(errs)

        # C: synthetic no-geometry EST project
        pg.evaluate(f"openEstimationRun({json.dumps(TMP_ID)})")
        pg.wait_for_timeout(3000)
        note = pg.locator(".im-noart")
        out["no_evidence_project"] = {
            "canvas": pg.locator("#imStage canvas").count(),
            "noart_note": note.count(),
            "noart_text": note.first.text_content() if note.count() else None,
            "im_art": pg.locator(".im-art").count(),
            "im_ctx": pg.locator(".im-ctx").count(),
            "visible_bucket_layers": pg.evaluate(UNSUPRESSED),
            "imRoot": pg.locator("#imRoot").count(),
            "footnote": pg.locator("#imLegend").inner_text()[:160] if pg.locator("#imLegend").count() else None,
        }
        out["nogeo_errors"] = [e for e in errs if e not in out["scene_errors"]]
        b.close()

    rep = (out["nogeo_representation"] or "").upper()
    schematic_class = rep in ("SITE_WORKS", "LINEAR", "NETWORK")
    checks = [
        ("A: scene_v2 intact", out["scene_v2_objects"] > 0),
        ("A: states labelled", bool(ks)),
        ("A: no-geo model has NO scene_v2", out["nogeo_scene_v2"] is False),
        ("B: boot clean", not out["boot_errors"]),
        ("B: no fake art nodes", out["scene_with_evidence"]["im_art"] == 0 and out["scene_with_evidence"]["im_ctx"] == 0),
        ("B: no visible schematic layers over 3D", out["scene_with_evidence"]["visible_bucket_layers"] == 0),
        ("B: 3D canvas mounts", out["scene_with_evidence"]["canvas"] > 0),
        ("B: no JS errors", not out["scene_errors"]),
        ("C: no canvas and no fake art", out["no_evidence_project"]["canvas"] == 0 and out["no_evidence_project"]["im_art"] == 0 and out["no_evidence_project"]["im_ctx"] == 0),
        ("C: no JS errors", not out["nogeo_errors"]),
    ]
    if schematic_class:
        checks.append(("C: schematic class renders data layers only", out["no_evidence_project"]["imRoot"] == 1))
        checks.append(("C: legend states schematic honestly", bool(out["no_evidence_project"]["footnote"])))
    else:
        checks.append(("C: honest noart note shown", out["no_evidence_project"]["noart_note"] == 1 and bool(out["no_evidence_project"]["noart_text"])))
        checks.append(("C: no invented layers", out["no_evidence_project"]["visible_bucket_layers"] == 0))
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except urllib.error.HTTPError as ex:
    out["ok"] = False
    out["fatal"] = f"HTTP {ex.code}: {ex.read().decode('utf-8','replace')[:300]}"
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)
finally:
    if os.path.exists(TMP_PACK):
        os.remove(TMP_PACK)
        out["tmp_pack_removed"] = True

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
