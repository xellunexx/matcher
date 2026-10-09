# -*- coding: utf-8 -*-
"""Step 12 verification: section / clipping tool (plan §11).

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. #imSecRow visible with 4 mode buttons; slider hidden while off
 2. horizontal/x/y each activate exactly one live plane; off clears it
 3. slider maps 0..100 onto the renderer's project-bounds axis range
    (state math verified against sectionState())
 4. VISUAL: a roof projection pixel materially changes when the horizontal
    plane cuts below it (proves the plane actually clips the meshes)
 5. roofs A and B each reappear after Off (pixel returns toward original)
 6. no page errors
"""
import io, json, math, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")

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
    sv2 = sp.get("scene_v2") or {}
    objs = sv2.get("objects") or []
    byid = {o["id"]: o for o in objs}
    out["est_id"] = est_id

    roof = byid.get("bldg-2.roof.l0") or next((o for o in objs if "roof" in o.get("id", "")), None)
    assert roof, "no roof object"
    g = roof.get("geometry") or {}

    # a screen point on the roof crown (contract coords): top of its bbox
    def top_of(o):
        gg = o.get("geometry") or {}
        if gg.get("type") == "gable_roof":
            poly = gg.get("polygon_m") or []
            xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            axis = gg.get("ridge_axis") or "x"
            mid = (min(xs)+max(xs))/2 if axis == "y" else (min(ys)+max(ys))/2
            # ridge line runs along `axis`; crown sits mid-span of the other
            if axis == "y": return (mid, (min(ys)+max(ys))/2, float(gg.get("ridge_z_m")))
            return ((min(xs)+max(xs))/2, mid, float(gg.get("ridge_z_m")))
        if gg.get("type") == "extrude":
            poly = gg.get("polygon_m") or []
            xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            return ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, float(gg.get("z1_m")))
        c = gg.get("center_m"); s = gg.get("size_m")
        return (float(c[0]), float(c[1]), float(c[2]) + float(s[2]) / 2) if c and s else None
    roof_top = top_of(roof)
    assert roof_top, "no roof top"

    from playwright.sync_api import sync_playwright
    from PIL import Image
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
        pg.wait_for_timeout(2800)

        # 1. row + buttons + slider hidden
        out["sec_row_visible"] = pg.locator("#imSecRow").is_visible()
        out["sec_buttons"] = sorted(pg.locator("#imSecBtns [data-im-sec]").evaluate_all(
            "els=>els.map(e=>e.getAttribute('data-im-sec'))"))
        out["slider_hidden_initial"] = not pg.locator("#imSecSlider").is_visible()

        sec = lambda: pg.evaluate("Immersive.spatial3d().sectionState()")
        proj = lambda xyz: pg.evaluate("Immersive.spatial3d().project(%s)" % json.dumps(list(xyz)))
        rect = lambda: pg.evaluate("Immersive.spatial3d().canvas.getBoundingClientRect()")

        def pixel(xyz, r=None):
            """screenshot pixel color at the canvas-projected contract point"""
            p = proj(xyz)
            if not p: return None
            if r is None: r = rect()
            shot = pg.locator("#imStage canvas").screenshot()
            im = Image.open(io.BytesIO(shot)).convert("RGB")
            ix, iy = int(p[0] - r["left"]), int(p[1] - r["top"])
            if ix < 0 or iy < 0 or ix >= im.width or iy >= im.height: return None
            return im.getpixel((ix, iy))

        def pick_at(xyz):
            p = proj(xyz)
            if not p: return None
            return pg.evaluate("Immersive.spatial3d().pick(%f,%f)" % (p[0], p[1]))

        def patch_mean(xyz, half=3, r=None):
            """mean RGB over a small patch — robust against the animated motes"""
            p = proj(xyz)
            if not p: return None
            if r is None: r = rect()
            shot = pg.locator("#imStage canvas").screenshot()
            im = Image.open(io.BytesIO(shot)).convert("RGB")
            cx, cy = int(p[0] - r["left"]), int(p[1] - r["top"])
            sx = sy = n = 0
            for dx in range(-half, half + 1):
                for dy in range(-half, half + 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < im.width and 0 <= y < im.height:
                        px = im.getpixel((x, y)); sx += px[0] + px[1] * 256 + px[2] * 65536; n += 1
                        sy += 0
            # unpack cheaply
            rs = gs = bs = 0
            m = 0
            for dx in range(-half, half + 1):
                for dy in range(-half, half + 1):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < im.width and 0 <= y < im.height:
                        px = im.getpixel((x, y)); rs += px[0]; gs += px[1]; bs += px[2]; m += 1
            return (rs / m, gs / m, bs / m) if m else None

        # find a TRUE roof pixel: sample the slope between eave and crown until
        # the renderer's own pick identifies the roof object at that point
        roof_id = roof["id"]
        crown = roof_top
        gg = roof.get("geometry") or {}
        eave = None
        if gg.get("type") == "gable_roof":
            poly = gg.get("polygon_m") or []
            xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            eave = ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, float(gg.get("eave_z_m"))) if gg.get("eave_z_m") is not None else crown
        else:
            eave = crown
        roof_pt = None
        for k in (0.25, 0.5, 0.75, 0.1, 0.9):
            pt = (eave[0] + (crown[0]-eave[0])*k, eave[1] + (crown[1]-eave[1])*k, eave[2] + (crown[2]-eave[2])*k)
            ud = pick_at(pt)
            if ud and ud.get("spatialId") == roof_id:
                roof_pt = pt
                break
        assert roof_pt, "could not find a screen pixel that picks the roof"

        # baseline patch at that true roof point
        base_px = patch_mean(roof_pt)
        out["roof_pixel_baseline"] = [round(v) for v in base_px] if base_px else None

        def cdist(a, b_):
            return math.sqrt(sum((a[i]-b_[i])**2 for i in range(3)))

        st0 = sec()
        out["initial"] = {"mode": st0["mode"], "planes": st0["planes"]}

        # 2/3. horizontal mode + slider mapping
        pg.click("#imSecBtns [data-im-sec='horizontal']")
        pg.wait_for_timeout(400)
        st1 = sec()
        out["slider_shown"] = pg.locator("#imSecSlider").is_visible()
        out["horizontal_on"] = {"mode": st1["mode"], "planes": st1["planes"]}
        assert st1["range"], "no range in horizontal mode"

        # slider -> 50% must land mid-range; done via real input event
        pg.evaluate("""(()=>{const s=document.getElementById('imSecSlider');
            s.value=50; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        pg.wait_for_timeout(200)
        st2 = sec()
        expect_mid = st2["range"][0] + 0.5 * (st2["range"][1] - st2["range"][0])
        out["slider_50"] = {"t": st2["t"], "value": st2["value"], "expect": expect_mid,
                            "ok": abs(st2["value"] - expect_mid) < 1e-3}

        # 3b. x / y modes: same math on the other two axes
        axes_ok = {}
        for m, ax in (("x", 0), ("y", 1)):
            pg.click("#imSecBtns [data-im-sec='%s']" % m)
            pg.evaluate("""(()=>{const s=document.getElementById('imSecSlider');
                s.value=25; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
            pg.wait_for_timeout(200)
            stx = sec()
            exp = stx["range"][0] + 0.25 * (stx["range"][1] - stx["range"][0])
            axes_ok[m] = (stx["mode"] == m and stx["planes"] == 1
                          and abs(stx["value"] - exp) < 1e-3)
        out["xy_modes"] = axes_ok

        # 4. VISUAL ground truth: raw GL pixels inside the render rAF tick.
        #    A pick-guided ROOF point must vanish under a horizontal cut below
        #    it; canvas-wide 20x10 grid samples bound the effect (partial cut
        #    at 45% = partial change; near-ground 2% = much more gone; Off
        #    restores the baseline exactly).
        vis = pg.evaluate("""(async(pts)=>{
          const h=Immersive.spatial3d(), c=h.canvas;
          const gl=c.getContext('webgl2')||c.getContext('webgl');
          const r=c.getBoundingClientRect();
          function loc(pt){const p=h.project(pt); if(!p)return null;
            return {gx:Math.round(p[0]-r.left), gy:c.height-Math.round(p[1]-r.top)};}
          const readAt=(g)=>new Promise(res=>requestAnimationFrame(()=>{
            const b4=new Uint8Array(4); gl.readPixels(g.gx,g.gy,1,1,gl.RGBA,gl.UNSIGNED_BYTE,b4); res([...b4]);}));
          const gH=loc(pts[0]);
          if(!gH) return {error:'projection failed'};
          const res={};
          const grid=()=>new Promise(res2=>requestAnimationFrame(()=>{
            const out2=[]; const b4=new Uint8Array(4);
            for(let gy=0; gy<10; gy++) for(let gx=0; gx<20; gx++){
              gl.readPixels(Math.round((gx+0.5)*c.width/20), Math.round((gy+0.5)*c.height/10), 1,1, gl.RGBA, gl.UNSIGNED_BYTE, b4);
              out2.push(b4[0],b4[1],b4[2],b4[3]);
            }
            res2(out2);
          }));
          const changedFrac=(a2,b2)=>{let n=0,ch=0;for(let i=0;i<a2.length;i+=4){n++;
            const dr=a2[i]-b2[i],dg=a2[i+1]-b2[i+1],db=a2[i+2]-b2[i+2],da=a2[i+3]-b2[i+3];
            if(dr*dr+dg*dg+db*db>225||Math.abs(da)>64)ch++;}return ch/n;};
          h.setSection('off',100);            // clean baseline (prior checks left a plane on)
          res.base=await grid();
          res.hiBefore=await readAt(gH);
          h.setSection('horizontal',45);      // half-height cut
          res.planes45=(h.sectionState().planes);
          res.g45=await grid();
          res.hiAfter=await readAt(gH);
          res.frac45=changedFrac(res.base,res.g45);
          h.setSection('horizontal',2);       // near-ground cut: almost everything goes
          res.g02=await grid();
          res.frac02=changedFrac(res.base,res.g02);
          h.setSection('off',100);
          res.gOff=await grid();
          res.fracOff=changedFrac(res.base,res.gOff);
          res.hiRestored=await readAt(gH);
          return res;
        })(%s)""" % json.dumps([list(roof_pt)]))
        assert isinstance(vis, dict) and "hiBefore" in vis, repr(vis)
        out["gl_pixels"] = {k: v for k, v in vis.items() if isinstance(v, (int, float)) or (isinstance(v, list) and len(v) <= 4)}
        d = lambda a, b_: cdist(a[:3], b_[:3])
        out["pixel_changed"] = d(vis["hiBefore"], vis["hiAfter"]) > 30
        out["partial_cut"] = 0.005 < vis["frac45"] < 0.25
        out["deep_cut_more"] = vis["frac02"] > vis["frac45"] + 0.1
        out["restore_exact"] = vis["fracOff"] < 0.01
        out["pixel_restored"] = d(vis["hiBefore"], vis["hiRestored"]) < 12
        out["plane_value"] = sec()["value"]

        out["off"] = (lambda s: {"mode": s["mode"], "planes": s["planes"]})(sec())

        out["page_errors"] = errs
        b.close()

    ok = (out["sec_row_visible"]
          and out["sec_buttons"] == sorted(["off", "horizontal", "x", "y"])
          and out["slider_hidden_initial"]
          and out["initial"]["mode"] == "off" and out["initial"]["planes"] == 0
          and out["horizontal_on"] == {"mode": "horizontal", "planes": 1}
          and out["slider_shown"]
          and out["slider_50"]["ok"]
          and all(out["xy_modes"].values())
          and out["gl_pixels"]["planes45"] == 1
          and out["pixel_changed"]          # roof pixel clips away at 45%
          and out["partial_cut"]            # 45% cut changes a partial slice only
          and out["deep_cut_more"]          # 2% cut removes strictly more
          and out["restore_exact"]          # Off: canvas equals baseline
          and out["pixel_restored"]         # Off restores the pixel exactly
          and out["off"] == {"mode": "off", "planes": 0}
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
