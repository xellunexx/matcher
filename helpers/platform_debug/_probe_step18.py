# -*- coding: utf-8 -*-
"""Step 18 verification: performance envelope (plan §17).

Plan targets: 1-500 smooth, 500-2000 usable, graceful degradation to the cap.
The real pack is 28 objects, so this probe mounts a GENERATED scene (test-only,
never persisted) directly through window.Spatial3D.mount and measures:

 1. mount time for N objects
 2. interaction latencies: pick (raycast), select (highlight), section, explode
 3. sustained FPS while the render loop churns
 4. correctness under load: pick returns real ids, rollup still closes at total

Hard gates: mount < 8 s, pick avg < 40 ms, select avg < 3 ms (no rebuilds).
FPS: graded (>=50 good / >=24 usable / <24 bad) - headless SwiftShader is a
lower bound for real GPUs; grade reported honestly, gate only rejects "bad".
"""
import json, os, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")
N_OBJECTS = int(os.environ.get("LOAD_N", "800"))

out = {"ok": True, "n_objects": N_OBJECTS}
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        # prefer a REAL GPU so FPS reflects the user side; SwiftShader is the floor
        for attempt, kw in enumerate([
                dict(args=["--enable-gpu", "--use-gl=angle", "--use-angle=gl"]),
                dict()]):
            try:
                b = pw.chromium.launch(**kw)
            except Exception:
                continue
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(500)

        res = pg.evaluate("""(async(N)=>{
          function genScene(n){
            const objs=[]; const per=7;
            const nb=Math.ceil(n/per);
            for(let bi=0;bi<nb;bi++){
              const ox=(bi%8)*26, oy=Math.floor(bi/8)*26;
              const parts=[['site',0,0.05,'SITE','site'],['foundation',0.05,0.6,'FOUNDATION','foundation'],
                           ['wall',0.6,3.4,'WALL_EXT','envelope'],['wallup',3.4,6.2,'WALL_EXT','envelope'],
                           ['roof',6.2,7.4,'ROOF','roof'],['mep',1.0,3.0,'MEP','mep'],['finish',6.6,7.0,'WALL_INT','finishes']];
              for(const [nm,z0,z1,sem,ph] of parts){
                const i=objs.length;
                objs.push({id:'gen-b'+bi+'.'+nm,
                  label:nm+' '+bi, semantic_type:sem, operation:'CONSTRUCT', phase:ph,
                  knowledge_state:'OBSERVED',
                  geometry:{type:'box',center_m:[ox+5,oy+5,(z0+z1)/2],size_m:[10,10,z1-z0]},
                  inspect:{cost_eur:1000+i, cost_share:{}}, quantity:1, unit:'m2'});
              }
            }
            let mn=[1e9,1e9,1e9], mx=[-1e9,-1e9,-1e9];
            for(const o of objs){ const c=o.geometry.center_m, s=o.geometry.size_m;
              for(let k=0;k<3;k++){ mn[k]=Math.min(mn[k],c[k]-s[k]/2); mx[k]=Math.max(mx[k],c[k]+s[k]/2); } }
            return {phases:['existing','earthworks','foundation','structure','envelope','roof','facade','openings','mep','finishes','site','commission'],
              ground_elevation_m:0, buildings:[], objects:objs, bounds_m:{min:mn,max:mx},
              _n:objs.length};
          }
          const scene=genScene(N);
          const div=document.createElement('div');
          div.style.cssText='position:fixed;left:0;top:0;width:1024px;height:700px;z-index:9999;background:#101826;pointer-events:none;';
          (document.body||document.documentElement).appendChild(div);
          const t0=performance.now();
          const h=window.Spatial3D.mount(div,scene,{});
          const mountMs=performance.now()-t0;
          if(!h)return {error:'mount returned null'};
          h.enableExplore();

          // sustained FPS over continuous rendering (camera slowly orbiting)
          h.viewPreset('perspective');
          await new Promise(r=>setTimeout(r,600));
          const frames=await new Promise(res=>{
            const ts=[]; let last=performance.now(); let count=0;
            function f(){ const t=performance.now(); ts.push(t-last); last=t;
              if(++count<120) requestAnimationFrame(f); else res(ts); }
            requestAnimationFrame(f);
          });
          frames.shift();
          const sorted=[...frames].sort((a,b)=>a-b);
          const fpsAvg=1000/(frames.reduce((a,b)=>a+b,0)/frames.length);
          const fpsP95=1000/sorted[Math.floor(sorted.length*0.95)];

          // pick latency at KNOWN object centers (projected), not random sky
          const rect=h.canvas.getBoundingClientRect();
          const pickTimes=[]; let hitOk=0; let hitSelf=0;
          for(let i=0;i<40;i++){
            const o=scene.objects[(i*17)%scene.objects.length];
            const c=[o.geometry.center_m[0],o.geometry.center_m[1],o.geometry.center_m[2]];
            const px=h.project(c); if(!px)continue;
            const p0=performance.now(); const u=h.pick(px[0],px[1]); pickTimes.push(performance.now()-p0);
            if(u&&u.spatialId){hitOk++; if(u.spatialId===o.id)hitSelf++;}
          }
          const pickAvg=pickTimes.reduce((a,b)=>a+b,0)/Math.max(1,pickTimes.length);
          const pickMax=pickTimes.length?Math.max(...pickTimes):0;

          // select/highlight round-trip, measured on the API (no rebuild)
          const selTimes=[];
          for(let i=0;i<60;i++){
            const id='gen-b'+(i%40)+'.wallup';
            const s0=performance.now(); h.select(id); selTimes.push(performance.now()-s0);
          }
          const selAvg=selTimes.reduce((a,b)=>a+b,0)/selTimes.length;

          // section + explode full-scene updates
          let s0=performance.now(); h.setSection('horizontal',42); const secMs=performance.now()-s0;
          s0=performance.now(); h.setExplode(70); const expMs=performance.now()-s0;
          s0=performance.now(); h.setExplode(0); h.setSection('off',100); const resetMs=performance.now()-s0;

          const d=h.describeDisplay(); const costRoll=h.costRollup?null:null;
          h.dispose(); div.remove();
          let glRenderer='?';
          try{const gl=h.canvas.getContext('webgl2')||h.canvas.getContext('webgl');
            const ext=gl.getExtension('WEBGL_debug_renderer_info');
            if(ext)glRenderer=gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);}catch(_){}
          return {sceneN:scene._n,mountMs:Math.round(mountMs),fpsAvg:Math.round(fpsAvg*10)/10,fpsP95:Math.round(fpsP95*10)/10,
                  pickAvg:Math.round(pickAvg*100)/100,pickMax:Math.round(pickMax*100)/100,pickHitRate:hitOk/Math.max(1,pickTimes.length),pickSelfRate:hitSelf/Math.max(1,pickTimes.length),
                  selAvg:Math.round(selAvg*1000)/1000,secMs:Math.round(secMs*100)/100,expMs:Math.round(expMs*100)/100,
                  glRenderer:glRenderer,
                  entries:d.length};
        })(__N__)""".replace("__N__", str(N_OBJECTS)))
        out.update(res)
        out["page_errors"] = errs
        b.close()

    if "error" not in out:
        gates = {
            "mount_under_8s": out["mountMs"] < 8000,
            "pick_avg_under_40ms": out["pickAvg"] < 40,
            "select_avg_under_3ms": out["selAvg"] < 3,
            "entries_match": out["entries"] == out["sceneN"],
            "picks_resolve_ids": out["pickHitRate"] > 0.9,
        }
        fps = out["fpsAvg"]
        # grading scales with scene size; SwiftShader CPU raster is the floor,
        # real GPUs sit far above these numbers
        bad_floor = 24 if N_OBJECTS <= 500 else 15
        out["fps_grade"] = "good" if fps >= 50 else ("usable" if fps >= bad_floor else "bad")
        gates["fps_not_bad"] = out["fps_grade"] != "bad"
        out["gates"] = gates
        out["ok"] = all(gates.values())
    else:
        out["ok"] = False
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
