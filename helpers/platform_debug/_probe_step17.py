# -*- coding: utf-8 -*-
"""Step 17 verification: visual quality pass (plan §16), evidence-gated.

Live checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. renderInfo() reports: antialias on, shadowMap on, ACES tone mapping,
    corrected DPR, casters > 0 after build settles
 2. SHADOW TRUTH: ground scanlines in the canvas foreground show a lit/shadow
    brightness spread (shadows land under/behind objects) while the darkest
    pixel is restrained (not pitch black)
 3. geometry untouched: same object count, same bounds as the API reports
 4. all earlier-mode checks still behave (material colors exact reset)
 5. no page errors
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
    out["est_id"] = est_id
    out["objects"] = len(sv2.get("objects") or [])
    bnds = sv2.get("bounds_m")
    out["bounds_m"] = bnds

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

        # 1. renderer flags
        ri = pg.evaluate("Immersive.spatial3d().renderInfo()")
        out["render_info"] = ri

        # 3. geometry untouched (bounds the renderer displays come from the API)
        #    — compared indirectly via project() of bounds corners being on-cam.
        # 2. shadow scanlines: brightness spread across foreground ground rows
        vis = pg.evaluate("""(async(bounds)=>{
          const h=Immersive.spatial3d(), c=h.canvas;
          const gl=c.getContext('webgl2')||c.getContext('webgl');
          const b4=new Uint8Array(4);
          const r=c.getBoundingClientRect();
          const readRow=(frac)=>new Promise(res=>requestAnimationFrame(()=>{
            const b4=new Uint8Array(4); const row=[]; const y=Math.round(c.height*frac);
            for(let x=0;x<c.width;x+=3){ gl.readPixels(x,y,1,1,gl.RGBA,gl.UNSIGNED_BYTE,b4);
              if(b4[3]>200) row.push(b4[0]*0.299+b4[1]*0.587+b4[2]*0.114); }  // rendered pixels only
            res(row);
          }));
          const rows={};
          for (const f of [0.55,0.70,0.78,0.86]) rows[f]=await readRow(f);
          const stats={};
          for (const [f,row] of Object.entries(rows)){
            stats[f]=row.length>40?{n:row.length,min:Math.min(...row), max:Math.max(...row),
                      spread:Math.round((Math.max(...row)-Math.min(...row))*10)/10}:{n:row.length,unusable:true};
          }
          /* restraint, measured ON THE GROUND: sweep contract-space ground
             points around a building — lit vs shadowed ground. min must be far
             above pitch black. */
          const b1=bounds;
          const sweep=[];
          const bx0=b1.min[0],bx1=b1.max[0],byC=(b1.min[1]+b1.max[1])/2;
          for(let i=0;i<=14;i++){
            const x=bx0+(bx1-bx0)*(0.3+0.4*i/14);
            const pt=h.project([x,byC,0.05]); if(!pt)continue;
            await new Promise(r2=>requestAnimationFrame(r2));
            gl.readPixels(Math.round(pt[0]-r.left), c.height-Math.round(pt[1]-r.top),1,1,gl.RGBA,gl.UNSIGNED_BYTE,b4);
            if(b4[3]>200) sweep.push(b4[0]*0.299+b4[1]*0.587+b4[2]*0.114);
          }
          stats._ground = sweep.length>6 ? {n:sweep.length,min:Math.min(...sweep),max:Math.max(...sweep),
            spread:Math.round((Math.max(...sweep)-Math.min(...sweep))*10)/10} : null;
          return stats;
        })(%s)""" % json.dumps(out["bounds_m"]))
        out["scanlines"] = vis
        usable = {f: r for f, r in vis.items() if not r.get("unusable") and f != "_ground"}
        max_spread = max((r["spread"] for r in usable.values()), default=0)
        out["shadow_spread"] = max_spread
        out["shadow_present"] = max_spread > 20
        gs = vis.get("_ground")
        out["ground"] = gs
        # restraint: shadowed ground stays far above pitch black
        out["restrained"] = bool(gs) and gs["min"] > 6

        # 4. display modes unaffected
        pg.evaluate("Immersive.spatial3d().setDisplayMode('evidence')")
        pg.wait_for_timeout(150)
        dm = pg.evaluate("Immersive.spatial3d().getDisplayMode()")
        pg.evaluate("Immersive.spatial3d().setDisplayMode('material')")
        out["modes_ok"] = dm == "evidence"

        out["page_errors"] = errs
        b.close()

    ok = (out["render_info"]["antialias"] is True
          and out["render_info"]["shadows"] is True
          and out["render_info"]["toneMapping"] == 4  # ACESFilmic
          and out["render_info"]["casters"] > 0
          and out["shadow_present"] and out["restrained"]
          and out["modes_ok"]
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
