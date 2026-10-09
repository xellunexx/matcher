# -*- coding: utf-8 -*-
"""Step 16 verification: evidence panel <-> 3D cross-linking (plan §15).

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. selecting an object opens the inspector with one evidence button per
    evidence entry {ref, locator}
 2. fact chips derive correctly from locators (footprint -> Placement,
    dimensions.* -> Height/storeys, .boq -> Scope/cost), in the active locale,
    and facts stay per-row distinct
 3. clicking an openable row calls the EXISTING /api/estimation/open endpoint
    with the file basename (fetch stubbed - no native window pops during tests)
 4. unsupported refs render as disabled rows (no bogus open attempts)
 5. the endpoint itself is live and validates (404 on a nonexistent .pdf)
 6. no page errors
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")
BG = {"footprint": "Разположение · контури",
      "height": "Височина · етажи",
      "boq": "Обхват · стойност (КСС)"}

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace"))

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
    out["est_id"] = est_id

    # pick an object with at least 2 evidence entries incl. a footprint + a dimensions.* locator
    def evlist(o):
        return [e for e in (o.get("evidence") or []) if isinstance(e, dict) and e.get("ref")]
    cand = None
    for o in objs:
        ev = evlist(o)
        kinds = {e.get("locator") for e in ev}
        if len(ev) >= 2 and any("footprint" in k for k in kinds if k) and any(k and k.startswith("dimensions.") for k in kinds):
            cand = o
            break
    assert cand, "no object with mixed footprint+dimensions evidence"
    oid = cand["id"]
    evs = evlist(cand)
    out["target"] = oid
    out["evidence_count"] = len(evs)

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

        # stub fetch BEFORE any evidence clicks (real endpoint must not launch native apps)
        pg.evaluate("""(()=>{window.__evCalls=[];
          window.fetch=(u,o)=>{window.__evCalls.push({u:String(u),body:o&&o.body});
            return Promise.resolve({status:200,json:()=>Promise.resolve({ok:true})});};return true;})()""")

        # 1. select object via its tree row -> inspector shows evidence buttons
        pg.locator('#imTree [data-imt="%s"]' % oid).scroll_into_view_if_needed()
        pg.locator('#imTree [data-imt="%s"]' % oid).click()
        pg.wait_for_timeout(400)
        rowsel = ".insp-evbtn"
        nrows = pg.locator(rowsel).count()
        out["evidence_rows"] = nrows
        out["rows_match_evidence"] = (nrows == len(evs))

        # 2. fact chips per row, locale-correct, facts stay distinct
        # (chips render uppercase via CSS text-transform — compare case-insensitively)
        chips = [c.lower() for c in pg.locator(rowsel + " .insp-fact").all_inner_texts()]
        locs = pg.locator(rowsel + " .insp-eveloc").all_inner_texts()
        chip_ok = 0
        chip_mismatch = []
        for i, e in enumerate(evs):
            loc = str(e.get("locator") or "")
            ref = str(e.get("ref") or "").lower()
            expect = None
            if "footprint" in loc or "site_boundary" in loc:
                expect = BG["footprint"].lower()
            elif loc.startswith("dimensions.") or "storeys" in loc or "floor_height" in loc:
                expect = BG["height"].lower()
            elif ref.endswith(".boq") or loc.startswith("vocab:"):
                expect = BG["boq"].lower()
            # find this row's chip (locator text matches the row's locator)
            row_chip = None
            for j in range(nrows):
                if j < len(locs) and locs[j] == loc:
                    cs = pg.locator(rowsel).nth(j)
                    f = cs.locator(".insp-fact")
                    row_chip = (f.inner_text() if f.count() else None)
                    row_chip = row_chip.lower() if isinstance(row_chip,str) else None
                    break
            if expect is None:
                if row_chip is not None:
                    chip_mismatch.append((loc, "unexpected chip"))
            elif row_chip != expect:
                chip_mismatch.append((loc, row_chip))
            else:
                chip_ok += 1
        out["chips"] = {"mapped_rows": chip_ok, "mismatch": chip_mismatch,
                        "distinct_facts": sorted(set(x for x in chips if x))}

        # 4. unsupported refs are disabled (checked on object 1's rows)
        disabled_n = pg.locator(rowsel + ":disabled").count()
        expected_disabled = sum(1 for e in evs
                                if str(e.get("ref") or "").lower().rsplit(".", 1)[-1]
                                not in ("xls", "xlsx", "pdf", "docx", "doc", "txt", "json", "csv", "png", "jpg", "jpeg"))
        out["disabled_rows"] = {"actual": disabled_n, "expected": expected_disabled}

        # 3. openable evidence lives elsewhere in the pack (refs here are .dxf,
        # intentionally NOT in the endpoint's allowlist) — find an object with a
        # supported ref (e.g. .pdf), open its inspector, stub-click, verify call.
        def openable(e):
            ext = str(e.get("ref") or "").lower().replace("\\", "/").rsplit(".", 1)
            ext = ext[-1] if "." in str(e.get("ref") or "") else ""
            return ext in ("xls", "xlsx", "pdf", "docx", "doc", "txt", "json", "csv", "png", "jpg", "jpeg")
        cand2 = next((o for o in objs if any(openable(e) for e in evlist(o))), None)
        right_call = False
        if cand2:
            # close object-1's inspector drawer first — it overlays the tree row
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(300)
            pg.locator('#imTree [data-imt="%s"]' % cand2["id"]).scroll_into_view_if_needed()
            pg.locator('#imTree [data-imt="%s"]' % cand2["id"]).click()
            pg.wait_for_timeout(400)
            evs2 = evlist(cand2)
            oi = next(i for i, e in enumerate(evs2) if openable(e))
            expect_fn = os.path.basename(str(evs2[oi]["ref"]).replace("\\", "/"))
            pg.locator(rowsel).nth(oi).click()
            pg.wait_for_timeout(250)
            calls = pg.evaluate("window.__evCalls")
            out["open_call"] = calls[-1] if calls else None
            right_call = (bool(calls) and calls[-1]["u"].endswith("/api/estimation/open")
                          and expect_fn in (calls[-1]["body"] or ""))
            out["active_row"] = "active" in (pg.locator(rowsel).nth(oi).get_attribute("class") or "")

        out["page_errors"] = errs
        b.close()

    # 5. endpoint exists, validates, rejects unknown files WITHOUT opening anything
    st, body = post("/api/estimation/open", {"file": "__probe_nonexistent__.pdf"})
    out["endpoint_contract"] = {"status": st, "ok": st == 404 and bool(body.get("error"))}
    st2, body2 = post("/api/estimation/open", {"file": "evil.txt.exe"})
    out["endpoint_rejects_bad_ext"] = {"status": st2, "ok": st2 in (404, 422)}

    ok = (out["rows_match_evidence"] and out["evidence_rows"] >= 2
          and not out["chips"]["mismatch"] and out["chips"]["mapped_rows"] >= 2
          and len(out["chips"]["distinct_facts"]) >= 2
          and (out["open_call"] and out["open_call"]["u"].endswith("/api/estimation/open"))
          and out["active_row"]
          and out["disabled_rows"]["actual"] == out["disabled_rows"]["expected"]
          and out["endpoint_contract"]["ok"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
