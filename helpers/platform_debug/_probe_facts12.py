# -*- coding: utf-8 -*-
"""FACTS 1+2 verification (live): HUD money is ledger-true across project
switches, with no stale viewer caches.

Two EST packs with DIFFERENT totals open back-to-back on :8123:
 A open → EXPLORE == A.total; BUILD mid-cursor HUD == A ledger fold.
 switch to B → EXPLORE == B.total EXACTLY (kills the 16k-vs-69k split screen:
 no stale S.tween / sparkCache residue from A);
 BUILD B start ≈ 0 (tween reset on bind);
 mid-cursor HUD == B ledger fold (geometry rollup override is gone);
 end HUD == B.total;
 sparkline canvas repaints for B (engine-keyed spark cache).
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def eur_to_float(txt):
    t = "".join(ch for ch in (txt or "") if ch.isdigit() or ch in ",.")
    t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except Exception:
        return None

def pack_info(est_id):
    sp = http("/api/estimation/spatial?id=" + est_id)
    total = float((sp.get("stats") or {}).get("totalExclVat") or 0)
    events = [ev for ev in ((sp.get("playback") or {}).get("events") or []) if isinstance(ev, dict)]
    ledger = [0.0] * (len(events) + 1)
    c = 0.0
    for i, ev in enumerate(events, start=1):
        if ev.get("project_cost_after") is not None:
            c = float(ev.get("project_cost_after") or 0)
        ledger[i] = c
    sv2 = sp.get("scene_v2") or {}
    return {"id": est_id, "total": total, "events": len(events), "ledger": ledger,
            "objects": len(sv2.get("objects") or [])}

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    ids = [h["id"] for h in hist["history"]]
    packs = []
    seen = set()
    for i in ids:
        p = pack_info(i)
        if p["events"] > 0 and p["objects"] > 0 and p["total"] > 0 and round(p["total"]) not in seen:
            seen.add(round(p["total"]))
            packs.append(p)
        if len(packs) >= 2:
            break
    if len(packs) < 2:
        # fall back to any two renderable packs with playback (totals may coincide)
        for i in ids:
            p = pack_info(i)
            if p["events"] > 0 and p["objects"] > 0 and all(p["id"] != q["id"] for q in packs):
                packs.append(p)
            if len(packs) >= 2:
                break
    assert len(packs) >= 2, "need ≥2 renderable history packs with playback"
    A, B = packs[0], packs[1]
    out["A"] = {k: A[k] for k in ("id", "total", "events", "objects")}
    out["B"] = {k: B[k] for k in ("id", "total", "events", "objects")}
    out["distinct_totals"] = round(A["total"]) != round(B["total"])

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

        def open_pack(p):
            pg.evaluate(f"openEstimationRun({json.dumps(p['id'])})")
            pg.wait_for_selector("#imStage canvas", timeout=30000)
            pg.wait_for_timeout(2500)

        def hud_cost():
            return eur_to_float(pg.locator("#imCost").inner_text()) if pg.locator("#imCost").count() else None

        def spark_png():
            return pg.evaluate("""(()=>{const c=document.getElementById('imSpark');
                return c&&c.toDataURL?c.toDataURL().length:null;})()""")

        def to_build():
            pg.evaluate("[...document.querySelectorAll('.im-modeb')].find(b=>b.getAttribute('data-im-mode')==='BUILD')?.click()")
            pg.wait_for_timeout(400)
            pg.evaluate("document.getElementById('imBuild')?.click()")
            pg.wait_for_timeout(400)

        def scrub(rng, wait=900):
            pg.evaluate(f"const r=document.getElementById('imRange'); r.value={rng}; r.dispatchEvent(new Event('input',{{bubbles:true}}));")
            pg.wait_for_timeout(wait)

        # ── pack A ──
        open_pack(A)
        a_explore = hud_cost()
        to_build()
        scrub(500)
        a_mid = hud_cost()
        a_mid_cursor = round(500 / 1000 * A["events"])
        a_mid_ledger = A["ledger"][a_mid_cursor]
        spark_A_mid = spark_png()

        # ── switch to pack B ──
        open_pack(B)
        # bind restores the persisted BUILD mode at cursor 0: HUD must be ~0 —
        # crucially NOT A's total (the 16k-vs-69k split-screen regression)
        b_open = hud_cost()
        spark_B_explore = spark_png()
        # explicit switch to EXPLORE (completed state) → full project total
        pg.evaluate("[...document.querySelectorAll('.im-modeb')].find(b=>b.getAttribute('data-im-mode')==='EXPLORE')?.click()")
        pg.wait_for_timeout(2500)
        b_explore = hud_cost()
        to_build()
        pg.evaluate("document.getElementById('imToStart')?.click()")
        pg.wait_for_timeout(900)
        b_start = hud_cost()            # tween reset holds: ≈ 0, not A's value
        scrub(500)
        b_mid = hud_cost()
        b_mid_cursor = round(500 / 1000 * B["events"])
        b_mid_ledger = B["ledger"][b_mid_cursor]
        scrub(1000, 1600)
        b_end = hud_cost()
        spark_B_end = spark_png()
        out.update({"a_explore": a_explore, "a_mid": a_mid, "a_mid_ledger": a_mid_ledger,
                    "b_open": b_open, "b_explore": b_explore, "b_start": b_start,
                    "b_mid": b_mid, "b_mid_ledger": b_mid_ledger, "b_end": b_end})
        b.close()

    tolA = max(25.0, 0.01 * A["total"])
    tolB = max(25.0, 0.01 * B["total"])
    checks = [
        ("A EXPLORE == A total", a_explore is not None and abs(a_explore - A["total"]) < 1.0),
        ("A mid HUD == A ledger fold (rollup override gone)", a_mid is not None and abs(a_mid - a_mid_ledger) <= tolA),
        ("B opens at ~0 — never A's stale total", b_open is not None and b_open < B["total"] * 0.02
            and (not out["distinct_totals"] or abs(b_open - A["total"]) > max(25.0, 0.5 * A["total"]))),
        ("B EXPLORE == B total exactly", b_explore is not None and abs(b_explore - B["total"]) < 1.0),
        ("B BUILD start ≈ 0 (tween reset on bind)", b_start is not None and b_start < B["total"] * 0.02),
        ("B mid HUD == B ledger fold", b_mid is not None and abs(b_mid - b_mid_ledger) <= tolB),
        ("B end HUD == B total", b_end is not None and abs(b_end - B["total"]) < 1.0),
        ("sparkline repainted for B", spark_A_mid is not None and spark_B_end is not None and spark_A_mid != spark_B_end),
        ("no JS errors", not errs),
    ]
    out["checks"] = [{"name": n, "pass": bool(p)} for n, p in checks]
    out["errors"] = list(errs)
    out["ok"] = all(p for _, p in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
