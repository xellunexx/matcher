# -*- coding: utf-8 -*-
"""Step 8 — P0 ACCEPTANCE GATE (plan §21), two-house EST project.
Server-side: buildings==2, DXF-derived positions, id grammar valid.
DOM (Playwright): per-building separate selection from real clicks; every
emitted component click-identifies its exact SpatialObject.id (using category
filters where occlusion lawfully applies); bare BUILDING root never wins a pick;
final BUILD cumulative == pricing.totalExclVat; OBSERVED vs INFERRED covered by
step-9 evidence view (checked there), fake-house absence checked here.
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

out = {"ok": True}
try:
    hist = http("/api/estimation/history")
    est_id = hist["history"][0]["id"]
    out["est_id"] = est_id

    sp = http("/api/estimation/spatial?id=" + est_id)
    sv2 = sp.get("scene_v2") or {}
    objs = sv2.get("objects") or []
    byid = {o["id"]: o for o in objs}

    checks = []
    def chk(name, cond):
        checks.append((name, bool(cond)))

    # ── server-side gate ──
    blds = sv2.get("buildings") or []
    chk("buildings == 2", len(blds) == 2)

    def centroid(o):
        g = o.get("geometry") or {}
        poly = g.get("polygon_m")
        if poly:
            xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            return (sum(xs)/len(xs), sum(ys)/len(ys))
        c = g.get("center_m")
        return (c[0], c[1]) if c else None
    c1 = centroid(byid.get("bldg-1.foundation.l0", {}))
    c2 = centroid(byid.get("bldg-2.foundation.l0", {}))
    dist = None
    if c1 and c2:
        dist = ((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2) ** 0.5
    chk("two distinct DXF-derived positions (>10m apart)", dist is not None and dist > 10)
    out["foundation_centroid_distance_m"] = dist

    def has_dxf_evidence(oid):
        o = byid.get(oid) or {}
        ev = o.get("evidence") or []
        return any(".dxf" in str(e.get("ref", "")).lower() for e in ev if isinstance(e, dict))
    chk("foundations carry DXF evidence refs", has_dxf_evidence("bldg-1.foundation.l0") and has_dxf_evidence("bldg-2.foundation.l0"))

    ids = [o["id"] for o in objs]
    chk("object IDs unique", len(ids) == len(set(ids)))
    chk("parents valid", all((o.get("parent") in byid) or (o.get("parent") is None) for o in objs))
    chk("phases valid", all(o.get("phase") in (sv2.get("phases") or []) for o in objs))
    chk("costs finite/nonnegative", all((o.get("inspect", {}).get("cost_eur") or 0) >= 0 for o in objs))

    # ── DOM side ──
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

        chk("no legacy fake art in DOM", pg.evaluate("document.querySelectorAll('.im-art,#imArt,.im-ctx').length") == 0)

        point_of = pg.evaluate("""(()=>{const h=Immersive.spatial3d();if(!h)return null;
          const box=document.querySelector('#imStage canvas').getBoundingClientRect();
          return {x0:box.x,y0:box.y,w:box.width,h:box.height};})()""")

        def map_points():  # dense pick sweep → {id: (x,y)} client coords
            r = pg.evaluate("""(()=>{const h=Immersive.spatial3d();const box=document.querySelector('#imStage canvas').getBoundingClientRect();
              const m={};
              for(let iy=0; iy<26; iy++)for(let ix=0; ix<34; ix++){
                const x=box.x+box.width*(ix+0.5)/34, y=box.y+box.height*(iy+0.5)/26;
                const p=h.pick(x,y);
                if(p&&p.spatialId&&m[p.spatialId]===undefined)m[p.spatialId]=[x,y];}
              return m;})()""")
            return r or {}

        def set_filter(cat):
            pg.evaluate(f"[...document.querySelectorAll('#imFilterChips [data-imf-cat]')].find(b=>b.getAttribute('data-imf-cat')==='{cat}')?.click()")
            pg.wait_for_timeout(450)

        def click_point(pt):
            pg.mouse.click(pt[0], pt[1])
            pg.wait_for_timeout(550)
            d = pg.locator("#drawer")
            txt = d.inner_text() if d.count() and d.get_attribute("aria-hidden") == "false" else ""
            pg.evaluate("closeDrawer && closeDrawer()")
            pg.wait_for_timeout(250)
            return txt

        # A/B separate selection by REAL clicks (no filters)
        pmap = map_points()
        roofA = pmap.get("bldg-1.roof.l0") or pmap.get("bldg-1.roof.l0.n1")
        roofB = pmap.get("bldg-2.roof.l0")
        selA = click_point(roofA) if roofA else ""
        chk("click roof A → exact id bldg-1.roof*", "bldg-1.roof" in selA)
        selB = click_point(roofB) if roofB else ""
        chk("click roof B → exact id bldg-2.roof*", "bldg-2.roof" in selB and "bldg-1" not in selB)

        # bare root never wins anywhere (all view)
        roots_hit = [i for i in pmap.keys() if i in ("bldg-1", "bldg-2")]
        chk("BUILDING root never wins a pick (EXPLORE, ALL)", not roots_hit)

        # component coverage via lawful filters
        targets = []  # (id, filter)
        for bk in ("bldg-1", "bldg-2"):
            for suf, cat in [("foundation.l0", "FOUNDATIONS"), ("slab.l1", "SLABS"), ("slab.l2", "SLABS"),
                             ("wall-ext.l1", "WALLS"), ("wall-ext.l2", "WALLS"), ("roof.l0", "ROOF"),
                             ("mep.l0", "MEP")]:
                oid = f"{bk}.{suf}"
                if oid in byid:
                    targets.append((oid, cat))
        for extra, cat in [("bldg-1.site.l0", "SITE"), ("site-1.fence.l0", "SITE"),
                           ("site-1.net-0", "NETWORKS"), ("site-1.net-1", "NETWORKS"), ("site-1.net-2", "NETWORKS")]:
            if extra in byid:
                targets.append((extra, cat))
        clicked, missed = [], []
        for oid, cat in targets:
            set_filter(cat)
            pm = map_points()
            pt = pm.get(oid)
            if not pt:
                missed.append(oid)
                continue
            txt = click_point(pt)
            (clicked if oid in txt else missed).append(oid)
            pg.evaluate("[...document.querySelectorAll('#imFilterChips [data-imf-cat]')].find(b=>b.getAttribute('data-imf-cat')==='ALL')?.click()")
        set_filter("ALL")
        chk(f"every emitted component click-identifies its exact SpatialObject.id ({len(clicked)}/{len(targets)})",
            not missed)
        out["components_clicked"] = clicked
        out["components_missed"] = missed

        # BUILD final cumulative == project total
        pg.evaluate("[...document.querySelectorAll('.im-modeb')].find(b=>b.getAttribute('data-im-mode')==='BUILD')?.click()")
        pg.wait_for_timeout(400)
        pg.evaluate("document.getElementById('imBuild')?.click()")
        pg.wait_for_timeout(300)
        pg.evaluate("const r=document.getElementById('imRange'); r.value=1000; r.dispatchEvent(new Event('input',{bubbles:true}));")
        pg.wait_for_timeout(1500)
        final_cost = eur_to_float(pg.locator("#imCost").inner_text())
        pack_total = float((sp.get("stats") or {}).get("totalExclVat") or 0)
        chk("final BUILD cumulative == project total (no double count)",
            final_cost is not None and abs(final_cost - pack_total) < 1.0)
        out["final_cost"] = final_cost
        out["pack_total"] = pack_total

        out["errors"] = list(errs)
        chk("no JS errors", not out["errors"])
        b.close()

    out["checks"] = [{"name": n, "pass": bool(c)} for n, c in checks]
    out["ok"] = all(c for _, c in checks)
except Exception as ex:
    out["ok"] = False
    out["fatal"] = repr(ex)

print(json.dumps(out, ensure_ascii=False, indent=1))
raise SystemExit(0 if out["ok"] else 1)
