# -*- coding: utf-8 -*-
"""Item-25 (revised): viewer tools live in the left sidebar under History.

Checks (PROBE_BASE, default http://127.0.0.1:8123):
 1. all five rows (ViewBy/Camera/Section/Explode/Measure) live INSIDE
    #imSideTools, which sits directly after the primary nav (under History)
    in #sidebar, above the Theme shade block; nothing overlays the stage
 2. dock hidden & empty when not in the immersive viewer (mode switch -> cleared)
 3. handlers still work from the sidebar: cost view, camera preset, measure
    toggle, section slider, explode slider
 4. sidebar block fits the ~213px width (no horizontal overflow)
 5. no page errors
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
    est_id = None
    for h in hist["history"]:
        probe = http("/api/estimation/spatial?id=" + h["id"])
        if len(((probe.get("scene_v2") or {}).get("buildings") or [])) == 2:
            est_id = h["id"]
            break
    assert est_id, "no two-house pack"

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1648, "height": 928})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(BASE + "/", wait_until="networkidle")
        pg.wait_for_timeout(500)

        # not yet in the viewer -> block hidden
        out["before_open"] = pg.evaluate("""(()=>{const d=document.getElementById('imSideTools');
          return d?{hidden:d.hidden, kids:d.children.length}:null;})()""")

        pg.evaluate("setMode('estimation')")
        pg.evaluate("openEstimationRun(%s)" % json.dumps(est_id))
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(2800)

        # 1. structure & geography vs History item and Theme shade
        out["structure"] = pg.evaluate("""(()=>{
          const d=document.getElementById('imSideTools');
          const sb=document.getElementById('sidebar');
          const navIt=[...document.querySelectorAll('#sidebar .nav-item')].find(b=>b.dataset.mode==='history');
          const shade=document.querySelector('#sidebar .shade-row');
          const rows=['imViewRow','imCamRow','imSecRow','imExplodeRow','imMeasRow'];
          const res={hidden:d.hidden, kids:d.children.length,
                     inSidebar:!!(d&&sb.contains(d)), overflowX:d.scrollWidth>d.clientWidth};
          for(const id of rows){
            const el=document.getElementById(id);
            res[id]={inside:!!(d&&d.contains(el)), visible:!!(el&&el.offsetParent!==null)};
          }
          const dr=d.getBoundingClientRect(), H=navIt.getBoundingClientRect(), sh=shade.getBoundingClientRect();
          res.geo={belowHistory:dr.top>H.bottom, aboveShade:dr.bottom<=sh.top+2,
                   sidebarTop:Math.round(dr.top), dockBottom:Math.round(dr.bottom), shadeTop:Math.round(sh.top)};
          return res;
        })()""")

        # 3. handlers from the sidebar
        pg.click("#imViewBtns [data-im-view='cost']")
        out["mode"] = pg.evaluate("Immersive.spatial3d().getDisplayMode()")
        pg.click("#imCamBtns [data-im-cam='top']")
        pg.wait_for_timeout(600)
        cam = pg.evaluate("Immersive.spatial3d().camState()")
        dy = cam["pos"][1] - cam["target"][1]
        tot = abs(cam["pos"][0]-cam["target"][0]) + abs(cam["pos"][1]-cam["target"][1]) + abs(cam["pos"][2]-cam["target"][2])
        out["cam_top"] = dy > 0.8 * tot
        pg.evaluate("Immersive.spatial3d().viewPreset('perspective')")
        pg.click("#imMeasRow [data-im-meas='3d']")
        out["meas_active"] = "active" in (pg.locator("#imMeasRow [data-im-meas='3d']").get_attribute("class") or "")
        pg.click("#imMeasRow [data-im-meas='3d']")
        pg.evaluate("""(()=>{const s=document.getElementById('imSecSlider');
            s.value=55; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        # section buttons
        pg.click("#imSecBtns [data-im-sec='horizontal']")
        pg.wait_for_timeout(200)
        out["sec_planes"] = pg.evaluate("Immersive.spatial3d().sectionState().planes")
        pg.click("#imSecBtns [data-im-sec='off']")
        pg.evaluate("""(()=>{const s=document.getElementById('imExplodeSlider');
            s.value=40; s.dispatchEvent(new Event('input',{bubbles:true}));})()""")
        pg.wait_for_timeout(150)
        out["explode_t"] = pg.evaluate("Immersive.spatial3d().explodeState().t")
        pg.evaluate("Immersive.spatial3d().setExplode(0)")

        # 2. leaving the viewer clears the block
        pg.evaluate("setMode('tenders')")
        pg.wait_for_timeout(400)
        out["after_leave"] = pg.evaluate("""(()=>{const d=document.getElementById('imSideTools');
          return {hidden:d.hidden, kids:d.children.length};})()""")
        # back again -> repopulated
        pg.evaluate("setMode('estimation')")
        pg.wait_for_timeout(300)
        pg.evaluate("openEstimationRun(%s)" % json.dumps(est_id))
        pg.wait_for_selector("#imStage canvas", timeout=30000)
        pg.wait_for_timeout(1800)
        out["after_return"] = pg.evaluate("""(()=>{const d=document.getElementById('imSideTools');
          return {hidden:d.hidden, kids:d.children.length};})()""")

        out["page_errors"] = errs
        b.close()

    st2 = out["structure"]
    ok = (out["before_open"]["hidden"] and out["before_open"]["kids"] == 0
          and not st2["hidden"] and st2["kids"] == 5 and st2["inSidebar"] and not st2["overflowX"]
          and all(st2[i]["inside"] and st2[i]["visible"]
                  for i in ("imViewRow", "imCamRow", "imSecRow", "imExplodeRow", "imMeasRow"))
          and st2["geo"]["belowHistory"] and st2["geo"]["aboveShade"]
          and out["mode"] == "cost" and out["cam_top"] and out["meas_active"]
          and out["sec_planes"] == 1 and out["explode_t"] == 40
          and out["after_leave"]["hidden"] and out["after_leave"]["kids"] == 0
          and not out["after_return"]["hidden"] and out["after_return"]["kids"] == 5
          and not out["page_errors"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
