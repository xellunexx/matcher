# -*- coding: utf-8 -*-
"""Step 6 verification, ledger-true revision (FACTS-1): the financial HUD follows
the engine's BOQ playback ledger, NOT the geometry rollup.

Two-house pack on :8123. Checks: EXPLORE shows the full project total; BUILD
starts near 0; HUD climbs monotonically while TRACKING THE LEDGER at each sampled
cursor (|HUD - Σcost_delta-fold| within tolerance); final HUD == totalExclVat
exactly. Sparse mid-playback money (HUMAN_INPUT_REQUIRED rows at 0€) is EXPECTED
here — a stall in the curve is honest, not a defect; what is forbidden is a HUD
value that contradicts the ledger at the same cursor.
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def eur_to_float(txt):
    t = "".join(ch for ch in (txt or "") if ch.isdigit() or ch in ",.")
    t = t.replace(".", "").replace(",", ".")  # 1 051 176,00 → 1051176.00
    try:
        return float(t)
    except Exception:
        return None

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = None
    for h in hist["history"]:
        probe = http("/api/estimation/spatial?id=" + h["id"])
        sv2 = probe.get("scene_v2") or {}
        tot = float((probe.get("stats") or {}).get("totalExclVat") or 0)
        if len(sv2.get("objects") or []) > 0 and len(sv2.get("buildings") or []) >= 2 and tot > 0 \
                and len(((probe.get("playback") or {}).get("events") or [])) > 0:
            sp = probe
            est_id = h["id"]
            break
    assert est_id, "no renderable playback pack (scene_v2 with objects + playback events) in history"
    totalExcl = float((sp.get("stats") or {}).get("totalExclVat") or 0)
    ca = ((sp.get("scene_v2") or {}).get("cost_allocation")) or {}
    pb = sp.get("playback") or {}
    events = [ev for ev in (pb.get("events") or []) if isinstance(ev, dict)]
    m = len(events)
    # ledger[cursor] = fold of events[0..cursor-1] (mirrors Engine.apply in web view)
    ledger = [0.0] * (m + 1)
    c = 0.0
    for i, ev in enumerate(events, start=1):
        if ev.get("project_cost_after") is not None:
            c = float(ev.get("project_cost_after") or 0)
        ledger[i] = c
    sum_delta = sum(float(ev.get("cost_delta") or 0) for ev in events)
    out["est_id"] = est_id
    out["totalExclVat"] = totalExcl
    out["boq_total"] = ca.get("boq_total_eur")
    out["unallocated"] = ca.get("unallocated_eur")
    out["ledger_events"] = m
    out["ledger_end"] = ledger[-1]
    out["sum_cost_delta"] = sum_delta

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m_: errs.append(m_.text) if m_.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(500)
        pg.evaluate("setMode('estimation')")
        pg.evaluate(f"openEstimationRun({json.dumps(est_id)})")
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(2500)

        samples = []
        def grab(tag, rng):
            cost = eur_to_float(pg.locator("#imCost").inner_text()) if pg.locator("#imCost").count() else None
            inst = pg.locator("#imInst").inner_text() if pg.locator("#imInst").count() else None
            pct = pg.locator("#imPct").inner_text() if pg.locator("#imPct").count() else None
            cur = round(rng / 1000 * m) if rng is not None else None
            samples.append({"tag": tag, "range": rng, "cursor": cur, "cost": cost,
                            "ledger": ledger[cur] if cur is not None else None,
                            "inst": inst, "pct": pct})

        grab("explore", None)  # EXPLORE = completed state → HUD shows full project total
        pg.evaluate("[...document.querySelectorAll('.im-modeb')].find(b=>b.getAttribute('data-im-mode')==='BUILD')?.click()")
        pg.wait_for_timeout(400)
        pg.evaluate("document.getElementById('imBuild')?.click()")
        pg.wait_for_timeout(400)
        pg.evaluate("document.getElementById('imToStart')?.click()")
        pg.wait_for_timeout(900)
        grab("start", 0)
        for want, tag in [(150, "early"), (500, "mid")]:
            pg.evaluate(f"const r=document.getElementById('imRange'); r.value={want}; r.dispatchEvent(new Event('input',{{bubbles:true}}));")
            pg.wait_for_timeout(900)
            grab(tag, want)
        pg.evaluate("const r=document.getElementById('imRange'); r.value=1000; r.dispatchEvent(new Event('input',{bubbles:true}));")
        pg.wait_for_timeout(1600)
        grab("end", 1000)
        done_big = pg.locator("#imDoneCard .im-donebig").inner_text() if pg.locator("#imDone:not([hidden]) .im-donebig").count() else None
        out["done_big"] = done_big
        out["samples"] = samples
        out["errors"] = list(errs)
        b.close()

    by = {s["tag"]: s for s in samples}
    def parse_pct(p):
        try:
            return float(str(p).replace("%", "").strip())
        except Exception:
            return None
    tol = max(25.0, 0.01 * totalExcl)  # easing settle + formatting tolerance
    def ledger_true(s):
        return (s is not None and s.get("cost") is not None and s.get("ledger") is not None
                and abs(s["cost"] - s["ledger"]) <= tol)
    runs = [by.get(k, {}) for k in ("start", "early", "mid", "end")]
    checks = [
        ("money data present (boq_total == totalExclVat)", totalExcl > 0 and out["boq_total"] == totalExcl),
        ("unallocated honestly reported, not hidden in HUD", out["unallocated"] is not None and out["unallocated"] >= 0),
        ("ledger complete: Σcost_delta == totalExclVat", abs(sum_delta - totalExcl) < 0.51),
        ("EXPLORE shows full project total", abs((by.get("explore", {}).get("cost") or -1) - totalExcl) < 1.0),
        ("BUILD start ≈ 0", (lambda c: c is not None and c < totalExcl * 0.02)(by.get("start", {}).get("cost"))),
        ("cost monotone non-decreasing", all(r["cost"] is not None and runs[i + 1]["cost"] is not None and r["cost"] <= runs[i + 1]["cost"] + 0.01 for i, r in enumerate(runs[:-1]))),
        ("HUD tracks ledger at early cursor", ledger_true(by.get("early"))),
        ("HUD tracks ledger at mid cursor", ledger_true(by.get("mid"))),
        ("end == project totalExclVat", abs((by.get("end", {}).get("cost") or 0) - totalExcl) < 1.0),
        ("installed counter present and full at end", bool(by.get("end", {}).get("inst")) and "/" in by["end"]["inst"] and by["end"]["inst"].split("/")[0].strip() == by["end"]["inst"].split("/")[1].strip()),
        ("percent hits 100% at end", parse_pct(by.get("end", {}).get("pct")) == 100),
        ("done card total matches", abs((eur_to_float(done_big) or 0) - totalExcl) < 1.0),
        ("no JS errors", not out["errors"]),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
