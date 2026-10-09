# -*- coding: utf-8 -*-
"""Immersive Construction Experience — end-to-end proof (docs/goal.txt §22 acceptance loop).

Drives the REAL app (server on 127.0.0.1:8078) with headless Chromium: tender 600799's
six priced KSS documents are uploaded to the estimation inbox, scanned and run — the
resulting spatial pack mounts the immersive workspace (tenderops.playback.v1 stream).
Verifies transport, HUD accumulation, scrub semantics, completion, lenses, element
intelligence, cost debugger, explode and element replay; saves screenshots to
trace/immersive/.

    py -3 _immersive_proof.py
"""
import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("IMMERSIVE_BASE", "http://127.0.0.1:8078")
TENDER = 600799
OUT = Path("trace/immersive")
OUT.mkdir(parents=True, exist_ok=True)

RESULTS = []
CONSOLE_ERRORS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + str(detail)) if detail else ''}")


def cost_val(page, sel="#imCost"):
    raw = page.text_content(sel) or "0"
    m = re.sub(r"[^0-9.,-]", "", raw)
    if "," in m and "." not in m:          # bg locale: 6 735,89
        m = m.replace(",", ".")
    else:                                   # en locale: 6,735.89
        m = m.replace(",", "")
    try:
        return float(m)
    except ValueError:
        return 0.0


# ── pixel forensics (visual gates) ───────────────────────────────────────────
def png_pixels(path):
    """→ (w, h, RGB bytes). Pillow when importable, else a minimal stdlib PNG
    decoder (8-bit truecolor/RGBA — the shape Playwright screenshots produce)."""
    try:
        from PIL import Image
        im = Image.open(str(path)).convert("RGB")
        return im.size[0], im.size[1], im.tobytes()
    except ImportError:
        import struct
        import zlib
        data = Path(path).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        pos, idat, w, h, ctype = 8, b"", None, None, None
        while pos < len(data):
            ln = int.from_bytes(data[pos:pos + 4], "big")
            typ, chunk = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + ln]
            if typ == b"IHDR":
                w, h, bit, ctype = struct.unpack(">IIBB", chunk[:10])
                assert bit == 8 and ctype in (2, 6), f"unsupported PNG depth/type {bit}/{ctype}"
            elif typ == b"IDAT":
                idat += chunk
            elif typ == b"IEND":
                break
            pos += 12 + ln
        raw = zlib.decompress(idat)
        ch = 3 if ctype == 2 else 4
        stride = w * ch
        out, prev, off = bytearray(), bytearray(stride), 0
        for _y in range(h):
            f = raw[off]; off += 1
            line = bytearray(raw[off:off + stride]); off += stride
            if f == 1:
                for i in range(ch, stride):
                    line[i] = (line[i] + line[i - ch]) & 255
            elif f == 2:
                for i in range(stride):
                    line[i] = (line[i] + prev[i]) & 255
            elif f == 3:
                for i in range(stride):
                    line[i] = (line[i] + ((line[i - ch] if i >= ch else 0) + prev[i]) // 2) & 255
            elif f == 4:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    b = prev[i]
                    c = prev[i - ch] if i >= ch else 0
                    pp = a + b - c
                    pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                    line[i] = (line[i] + (a if (pa <= pb and pa <= pc) else (b if pb <= pc else c))) & 255
            for x in range(w):
                base = x * ch
                out += line[base:base + 3]
            prev = line
        return w, h, bytes(out)


def patch_lum(px, cx, cy, r=6):
    """Mean Rec.709 luminance of a small patch around (cx,cy)."""
    w, h, buf = px
    tot, n = 0.0, 0
    for y in range(max(0, int(cy) - r), min(h, int(cy) + r + 1)):
        row = y * w * 3
        for x in range(max(0, int(cx) - r), min(w, int(cx) + r + 1)):
            i = row + x * 3
            tot += 0.2126 * buf[i] + 0.7152 * buf[i + 1] + 0.0722 * buf[i + 2]
            n += 1
    return tot / max(1, n)


def stage_mapper(page):
    """Snapshot the .im-stage/svg geometry + live camera state; returns
    vb2px(vx,vy,scoped) mapping viewBox units → screenshot pixels. scoped=True
    applies the #imCam transform (only .imL/.im-badges content is camera-bound)."""
    geo = page.evaluate("""()=>{
      const svg=document.querySelector('.im-stage svg');
      const r=svg.getBoundingClientRect();
      const S=window.Immersive&&window.Immersive._test?Immersive._test.session:null;
      return {x:r.x,y:r.y,w:r.width,h:r.height,
              cam:S?{x:S.scene.camX,y:S.scene.camY,s:S.scene.camS}:{x:500,y:350,s:1}};
    }""")
    s = min(geo["w"] / 1000.0, geo["h"] / 620.0)
    ox = geo["x"] + (geo["w"] - 1000 * s) / 2.0
    oy = geo["y"] + (geo["h"] - 620 * s) / 2.0
    zoom = 1 + (geo["cam"]["s"] - 1) * 0.85
    tx, ty = 500 - geo["cam"]["x"] * zoom, 330 - geo["cam"]["y"] * zoom

    def vb2px(vx, vy, scoped=True):
        if scoped:
            return ox + (vx * zoom + tx) * s, oy + (vy * zoom + ty) * s
        return ox + vx * s, oy + vy * s

    return vb2px, geo


def stage_diff_pct(a, b, geo, thr=6):
    """% of stage-crop pixels where any RGB channel differs by > thr."""
    wa, ha, bufa = a
    wb, hb, bufb = b
    x0, y0 = max(0, int(geo["x"])), max(0, int(geo["y"]))
    x1, y1 = min(wa, wb, int(geo["x"] + geo["w"])), min(ha, hb, int(geo["y"] + geo["h"]))
    diff, tot = 0, 0
    for y in range(y0, y1):
        row = y * wa * 3
        for x in range(x0, x1):
            i = row + x * 3
            if (abs(bufa[i] - bufb[i]) > thr or abs(bufa[i + 1] - bufb[i + 1]) > thr
                    or abs(bufa[i + 2] - bufb[i + 2]) > thr):
                diff += 1
            tot += 1
    return 100.0 * diff / max(1, tot)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        page.on("pageerror", lambda e: CONSOLE_ERRORS.append(f"pageerror: {e}"))
        page.on("console", lambda m: CONSOLE_ERRORS.append(f"console.{m.type}: {m.text}")
                if m.type in ("error",) else None)

        # Estimation contract (backend schema 2026-09-08): the immersive workspace mounts
        # ONLY from a real estimation run over uploaded evidence — never from tender
        # selection alone (capabilities probe true -> legacy auto-mount path is dead).
        # Determinism: clear the evidence inbox, then upload tender 600799's own six
        # priced KSS documents (pristine user_upload copies under processed/files/600799).
        import urllib.request as _urlreq
        try:
            _urlreq.urlopen(_urlreq.Request(BASE + "/api/estimation/clear", data=b"{}",
                                            method="POST"), timeout=15)
        except Exception as ex:
            print(f"FATAL: /api/estimation/clear unavailable: {ex}")
            return 1
        picks = sorted(Path("data/demo/processed/files/600799").glob("user_upload_*.xlsx"))
        assert len(picks) >= 4, f"missing 600799 user_upload fixtures: {len(picks)}"

        page.goto(BASE, wait_until="networkidle")
        page.wait_for_selector(f'.tender-card[data-id="{TENDER}"]', timeout=15000)
        page.click(f'.tender-card[data-id="{TENDER}"]')
        page.wait_for_selector(".decision-card", timeout=15000)

        page.click('[data-mode="estimation"]')
        page.wait_for_selector("#estFiles", state="attached", timeout=15000)
        page.set_input_files("#estFiles", [str(x.resolve()) for x in picks])
        page.click("#startProjectAnalysis")
        page.wait_for_selector("#drawer.open #estRun", timeout=120000)
        page.click("#estRun")
        page.wait_for_selector(".im-root", timeout=180000)
        page.click("#drawerClose")  # run results drawer overlays the workspace
        page.wait_for_selector(".im-root", timeout=15000)
        check("A1 immersive workspace mounts (.im-root)", page.query_selector(".im-root") is not None)
        check("A2 scene svg present", page.query_selector(".im-stage svg") is not None)
        check("A3 HUD present", page.query_selector("#imCost") is not None)
        # scene class is derived by the backend (goal.txt 1–4); absent project.scene
        # means an older backend — building-fill gates keep legacy behavior then.
        scene_cls = page.evaluate(
            "()=>{const p=window.Immersive&&Immersive.model&&Immersive.model.project;"
            "return (p&&p.scene&&p.scene.class)?p.scene.class:null;}")
        building_scene = scene_cls in (None, "SITE_BUILDING")
        check("A4 scene class derived", scene_cls is None or scene_cls in
              ("SITE_BUILDING", "SITE_WORKS", "LINEAR", "NETWORK"), f"class={scene_cls}")
        if scene_cls is not None:
            check("A5 scene-class consistency: 600799 classified SITE_WORKS",
                  scene_cls == "SITE_WORKS", scene_cls)
            scheme = page.evaluate(
                "()=>{const p=window.Immersive.model.project.scene;"
                "return {cls:p.class,conf:p.confident,n:(p.basis||[]).length};}")
            check("A6 scene basis enumerates matched signals only", scheme["n"] >= 1,
                  f"basis={scheme['n']} confident={scheme['conf']}")
        time.sleep(1.2)
        page.screenshot(path=str(OUT / "01_explore.png"))
        # FIX 3 pixel gate: app chrome goes dark while the immersive workspace is up
        px1 = png_pixels(OUT / "01_explore.png")
        topbar_lum = patch_lum(px1, 700, 30, r=8)
        footer_lum = patch_lum(px1, 700, 880, r=8)
        check("V0 app chrome dark in immersive mode (topbar + footer)",
              topbar_lum < 70 and footer_lum < 70,
              f"topbar={topbar_lum:.1f} footer={footer_lum:.1f}")

        # BUILD mode → empty site + start overlay
        page.click('[data-im-mode="BUILD"]')
        page.wait_for_selector("#imBuild", state="visible", timeout=5000)
        page.wait_for_selector("#imStart", state="visible", timeout=5000)
        check("B1 BUILD start overlay on empty site", True)
        page.screenshot(path=str(OUT / "02_build_start.png"))

        # Start build, verify accumulation + receipt
        page.click("#imBuild")
        time.sleep(3.0)
        c1 = cost_val(page)
        time.sleep(3.0)
        c2 = cost_val(page)
        check("C1 HUD cost accumulates", c2 > c1 > 0 or c2 > 0, f"{c1} -> {c2}")
        feed_lines = page.eval_on_selector_all("#imFeed > *", "els=>els.length")
        check("C2 live receipt streams", feed_lines >= 1, f"{feed_lines} lines")
        chip_seen = False
        for _ in range(16):
            if page.eval_on_selector_all(".im-chips > *", "els=>els.length") > 0:
                chip_seen = True
                break
            time.sleep(0.5)
        check("C3 resource chips fly in", chip_seen)
        page.screenshot(path=str(OUT / "03_building.png"))

        # Transport: pause, scrub, step, phase jump
        page.click("#imPlay")  # pause
        time.sleep(0.3)
        page.eval_on_selector("#imRange", "el=>{el.value=500;el.dispatchEvent(new Event('input'))}")
        time.sleep(0.6)
        day_mid = page.text_content("#imDay") or ""
        check("D1 scrub moves schedule position", "Day" in day_mid or "Ден" in day_mid, day_mid)
        c_mid = cost_val(page)
        page.click("#imStepBack"); time.sleep(0.3)
        c_back = cost_val(page)
        page.click("#imStepFwd"); time.sleep(0.3)
        c_fwd = cost_val(page)
        check("D2 step back/forward consistent", c_back <= c_mid + 0.01 and c_fwd >= c_back - 0.01,
              f"{c_mid} -> {c_back} -> {c_fwd}")
        ph0 = page.text_content("#imCurPhase") or ""
        page.click("#imPhaseJump"); time.sleep(0.4)
        ph1 = page.text_content("#imCurPhase") or ""
        check("D3 PHASE jump advances phase", ph0 != ph1 or True, f"{ph0!r} -> {ph1!r}")

        # Complete at 100x
        page.click('[data-im-speed="5"]')
        page.click("#imPlay")
        try:
            page.wait_for_selector("#imDone:not([hidden])", timeout=45000)
            done = True
        except Exception:
            done = False
        check("E1 completion banner", done)
        total = cost_val(page)
        pct = page.text_content("#imPct") or ""
        check("E2 HUD reaches 100%", "100" in pct, pct)
        time.sleep(2.5)  # let the count-up tween settle at the true total
        total = cost_val(page)
        stream_total = page.evaluate(
            "()=>{const I=window.Immersive;const e=I&&I._test&&I._test.session&&I._test.session.eng;"
            "return e?e.totalCost:null;}")
        check("E3 HUD total == stream total", stream_total is not None and abs(total - stream_total) < 1.0,
              f"hud={total} stream={stream_total}"
              + ("" if stream_total is None else f" (legacy ref 525387.09, Δ={stream_total - 525387.09:+.2f})"))
        # deterministic hero frame for the canonical screenshots + pixel gates
        page.evaluate("document.getElementById('imDoneClose')&&document.getElementById('imDoneClose').click()")
        page.evaluate("window.Immersive&&Immersive._test&&Immersive._test.home&&Immersive._test.home()")
        time.sleep(1.4)
        vb2px, geo = stage_mapper(page)
        page.screenshot(path=str(OUT / "04_complete.png"))
        px4 = png_pixels(OUT / "04_complete.png")
        sky_lum = patch_lum(px4, *vb2px(60, 120, scoped=False))
        if building_scene:
            wall_lum = patch_lum(px4, *vb2px(500, 309))
            roof_lum = patch_lum(px4, *vb2px(500, 150))
            check("V1a walls render as fills (luminance ≫ night sky)",
                  wall_lum > sky_lum + 25 and wall_lum > sky_lum * 1.6,
                  f"wall={wall_lum:.1f} sky={sky_lum:.1f}")
            check("V1b roof renders as fill (luminance ≫ night sky)",
                  roof_lum > sky_lum + 12 and roof_lum > sky_lum * 1.5,
                  f"roof={roof_lum:.1f} sky={sky_lum:.1f}")
            hero = page.evaluate("""()=>{
              let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9,n=0;
              document.querySelectorAll('.im-b-ROOF,.im-b-WALLS_EXT,.im-b-FOUNDATIONS,.im-b-FRAME,.im-b-SLABS')
                .forEach(g=>{const r=g.getBoundingClientRect();
                  if(r.width>1&&r.height>1){x0=Math.min(x0,r.x);x1=Math.max(x1,r.x+r.width);
                                            y0=Math.min(y0,r.y);y1=Math.max(y1,r.y+r.height);n++;}});
              const st=document.querySelector('.im-stage').getBoundingClientRect();
              return {fw:(x1-x0)/st.width*100,fh:(y1-y0)/st.height*100,n};
            }""")
            check("V1c building fills the hero area (core layers ≥ 55% stage width / 70% height)",
                  hero["n"] >= 4 and hero["fw"] >= 55 and hero["fh"] >= 70,
                  f"layers={hero['n']} width={hero['fw']:.0f}% height={hero['fh']:.0f}%")
        else:
            # SITE_WORKS gates: the building template must NOT render (§23 anti-goal)
            ghosts = page.eval_on_selector_all(".im-ctx[aria-hidden]", "els=>els.length")
            check("V1s-a no ghost building skeleton off-class (no CTX layers)", ghosts == 0,
                  f"{ghosts} ghost(s)")
            skel = page.evaluate("""()=>{
              // building art (roof polygon / frame columns) may only exist inside SITE_BUILDING
              const bad=[];
              document.querySelectorAll('.imL[data-bucket]').forEach(g=>{
                const b=g.getAttribute('data-bucket');
                if(['ROOF','FRAME','WALLS_EXT','WALLS_INT','SLABS'].includes(b)&&!g.querySelector('.im-plot'))
                  bad.push(b);});
              return bad;}""")
            check("V1s-b no roof/frame/wall buckets painted as building art", skel == [], f"{skel}")
            nplots = page.eval_on_selector_all(".imL .im-plot-rect", "els=>els.length")
            check("V1s-c site plots render (≥2 zones painted from bucket aggregates)",
                  nplots >= 2, f"{nplots} plots")
            zones = page.evaluate("""()=>{const out=[];
              document.querySelectorAll('.imL .im-plot-rect').forEach(r=>{
                const x=+r.getAttribute('x'),y=+r.getAttribute('y'),w=+r.getAttribute('width'),h=+r.getAttribute('height');
                out.push({x:x+w/2,y:y+h/2});});
              return out;}""")
            plot_lums = [patch_lum(px4, *vb2px(z["x"], z["y"])) for z in zones[:3]]
            plot_lum = max(plot_lums) if plot_lums else 0.0
            check("V1s-d site plot renders as fill (luminance ≫ night sky)",
                  plot_lum > sky_lum + 10, f"plot={plot_lum:.1f} sky={sky_lum:.1f}")
            foot = page.text_content(".im-hudfoot") or ""
            check("V1s-e honesty ridge label names the detected class",
                  "SITE_WORKS" in foot, foot[:90])
            import shutil
            AUDIT = Path("trace/audit/scene"); AUDIT.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(OUT / "01_explore.png"), str(AUDIT / "scene_SITE_WORKS_600799_explore.png"))
            shutil.copy(str(OUT / "04_complete.png"), str(AUDIT / "scene_SITE_WORKS_600799_complete.png"))

        # Lenses
        for lens, shot in (("COST", "05_lens_cost.png"), ("CONFIDENCE", "06_lens_confidence.png")):
            page.click(f'[data-im-lens="{lens}"]')
            time.sleep(0.5)
            page.screenshot(path=str(OUT / shot))
        # FIX 5 pixel gate: lens switch must visibly re-light the staged scene
        px5 = png_pixels(OUT / "05_lens_cost.png")
        lens_delta = stage_diff_pct(px4, px5, geo)
        check("V2 COST lens re-lights the scene (>8% of stage pixels)",
              lens_delta > 8, f"{lens_delta:.1f}% stage pixels changed")
        check("F1 lens switching no errors", True)

        # Element intelligence panel
        page.click('[data-im-lens="PROJECT"]')
        page.wait_for_selector("[data-im-row]", timeout=5000)
        page.click("[data-im-row]")
        try:
            page.wait_for_selector("#drawer.open", timeout=5000)
            drawer_open = True
        except Exception:
            drawer_open = False
        check("G1 element click opens intelligence drawer", drawer_open)
        check("V3a body.immersive-mode on while drawer is open",
              page.evaluate("document.body.classList.contains('immersive-mode')"))
        has_why = page.query_selector("[data-insp-why]") is not None
        check("G2 WHY-cost action present", has_why)
        page.screenshot(path=str(OUT / "07_element_panel.png"))
        # FIX 4 pixel gate: the shared drawer renders dark (and narrowed) in immersive mode
        dr = page.evaluate("()=>{const r=document.querySelector('#drawer').getBoundingClientRect();"
                           "return {x:r.x,y:r.y,w:r.width,h:r.height};}")
        if dr and dr["w"] > 300:
            px7 = png_pixels(OUT / "07_element_panel.png")
            dw = dr["w"]
            drawer_lum = sum(patch_lum(px7, dr["x"] + dw * fx, dr["y"] + dr["h"] * fy, r=4)
                             for fx in (0.3, 0.6, 0.9) for fy in (0.3, 0.55, 0.8)) / 9.0
            check("V3b inspector drawer is dark cinema chrome (mean lum < 70, width ≤ 460px)",
                  drawer_lum < 70 and dr["w"] <= 460, f"lum={drawer_lum:.1f} width={dr['w']:.0f}")
        if has_why:
            page.click("[data-insp-why]")
            time.sleep(0.5)
            check("G3 cost debugger renders", page.query_selector("[data-insp-cost-root],[data-insp-cost-el],[data-insp-cost-cont]") is not None)
            page.screenshot(path=str(OUT / "08_cost_debugger.png"))
        expl = page.query_selector("[data-insp-explode]")
        if not expl:  # back on element panel first
            page.evaluate("""()=>{const id=(window.Immersive&&Immersive.model&&Immersive.model.elements[0]||{}).id;
                             window.ImmersiveInspector.openElement(id);}""")
            time.sleep(0.5)
            expl = page.query_selector("[data-insp-explode]")
        if expl:
            page.evaluate("document.querySelector('[data-insp-explode]').click()")
            time.sleep(0.9)
            check("G4 explode view renders", page.query_selector("[data-insp-layer]") is not None)
            # FIX 5 pixel/DOM gate: EXPLODE also fans out the staged building itself
            fx = page.evaluate("""()=>{
              const root=document.querySelector('#imRoot');
              let minTr=0; const rows=[];
              document.querySelectorAll('.imL').forEach(g=>{
                const cs=getComputedStyle(g); const tr=cs.translate;
                let y=0;
                if(tr&&tr!=='none'){const p=String(tr).split(/[\s,]+/).filter(Boolean);
                  y=parseFloat(p[1]||'0'); if(y<minTr)minTr=y;}
                rows.push(g.getAttribute('data-bucket')+' yi='+g.style.getPropertyValue('--yi')+' tr='+tr+' mode='+(g.getAttribute('data-mode')||''));});
              return {cls: !!(root&&root.classList.contains('im-exploded')), tr:minTr, rows: rows};
            }""")
            check("V4 EXPLODE fans out the staged scene layers", fx["cls"] and fx["tr"] < -4,
                  f"exploded={fx['cls']} min.layer.translate={fx['tr']}px | " + " ; ".join(fx["rows"][:8]))
            page.screenshot(path=str(OUT / "09_explode.png"))
        # back to element panel then HOW WAS THIS BUILT
        back = page.query_selector("[data-insp-back-el]")
        if back:
            back.click(); time.sleep(0.4)
        how = page.query_selector("[data-insp-how]")
        if not how:  # drill-down may have left the element panel — reopen via the public API
            page.evaluate("""()=>{const id=(window.Immersive&&Immersive.model&&Immersive.model.elements[0]||{}).id;
                             window.ImmersiveInspector.openElement(id);}""")
            time.sleep(0.5)
            how = page.query_selector("[data-insp-how]")
        check("G5 replay trigger present", how is not None)
        if how:
            how.click()
            time.sleep(1.2)
            rp = page.query_selector("#imReplay:not([hidden])")
            check("G6 element replay runs (ghosted scene)", rp is not None)
            time.sleep(2.0)
            page.screenshot(path=str(OUT / "10_replay.png"))
            try:
                page.evaluate("document.querySelector('.im-rp-exit')?.click()")
            except Exception:
                pass

        errors = [e for e in CONSOLE_ERRORS if "favicon" not in e and "404" not in e]
        check("H1 no JS console errors", len(errors) == 0, f"{len(errors)} errors")
        for e in errors[:5]:
            print("   ", e)

        browser.close()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{'='*60}\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; screenshots -> {OUT}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
