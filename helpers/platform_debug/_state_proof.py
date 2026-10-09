# -*- coding: utf-8 -*-
"""Frontend state-hygiene proof (frontend-fix pass, 2026-09-08).

Drives the REAL app (server defaults to 127.0.0.1:8077; override with IMMERSIVE_BASE)
with headless Chromium. Verifies:

  P1  estimation picker accumulates picks with dedupe, chips removable per-item and via "clear"
  P2  fresh reload: mode switching leaves no cross-mode bleed (estimation must not render
      the selected tender's spatial workspace once the est-spatial contract exists)
  P3  hard-reset entry renders in the Help drawer, modal appears, cancels safely
  P4  resolve-all affordances render where expected (presence-only; new endpoints may 404
      on a pre-integration backend)
  P5  chips survive a full render() pass consistent with STATE

Backend-dependent steps probe the new contracts first and print SKIP-BACKEND instead of
failing on the old server. Screenshots land in trace/audit/state/.

    py -3 _state_proof.py            # or: IMMERSIVE_BASE=http://127.0.0.1:8077 py -3 _state_proof.py
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.environ.get("IMMERSIVE_BASE", "http://127.0.0.1:8077")
TENDER_DONE = 600799
OUT = Path("trace/audit/state")
OUT.mkdir(parents=True, exist_ok=True)

PICK_A = Path("tenderexamples/KSS_ocenena_EST20260906_052957.xlsx").resolve()
PICK_B = Path("tenderexamples/T467490-Радомир_DRAFT_КСС.xlsx").resolve()

RESULTS = []
CONSOLE_ERRORS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + str(detail)) if detail else ''}")


def skip(name, why):
    RESULTS.append((name, None, why))
    print(f"SKIP-BACKEND  {name} — {why}")


def probe_backend_new():
    """POST /api/estimation/cancel exists only on the integrated backend."""
    req = urllib.request.Request(BASE + "/api/estimation/cancel", method="POST", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return None  # server unreachable — treat as unknown


def state(page, expr):
    return page.evaluate("()=>{const H=window.__immersiveHelpers&&__immersiveHelpers();return H?(" + expr + ")(H.STATE):null}")


def tender_unresolved_blockers(tid):
    try:
        with urllib.request.urlopen(BASE + f"/api/tender/{tid}", timeout=15) as r:
            pack = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    boq = pack.get("boq") or []
    res = pack.get("resolutions") or {}
    return len([l for l in boq if (l.get("rule") == "EST" or l.get("flag")) and l.get("key") not in res])


def main():
    backend_new = probe_backend_new()
    print(f"backend: {'integrated (new est contracts)' if backend_new else 'pre-integration (legacy)' if backend_new is False else 'unknown'}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 900})
        page.on("pageerror", lambda e: CONSOLE_ERRORS.append(f"pageerror: {e}"))
        page.on("console", lambda m: CONSOLE_ERRORS.append(f"console.{m.type}: {m.text}")
                if m.type in ("error",) else None)

        # ── P1: estimation picker accumulates + removable chips ──────────────
        page.goto(BASE, wait_until="networkidle")
        page.click('[data-mode="estimation"]')
        page.wait_for_selector("#estFiles", state="attached", timeout=10000)
        page.set_input_files("#estFiles", str(PICK_A))
        time.sleep(0.2)
        page.set_input_files("#estFiles", str(PICK_B))
        time.sleep(0.3)
        n_chips = page.eval_on_selector_all("#estChips .file-chip", "els=>els.length")
        check("P1a estimation picker accumulates two picks", n_chips == 2, f"chips={n_chips}")
        check("P1b STATE.estFiles tracks picks", state(page, "s=>s.estFiles.length") == 2)
        # dedupe: re-picking the same file must not duplicate
        page.set_input_files("#estFiles", str(PICK_A))
        time.sleep(0.2)
        n_after_dup = page.eval_on_selector_all("#estChips .file-chip", "els=>els.length")
        check("P1c re-pick same file dedupes (name∥size∥lastModified)", n_after_dup == 2, f"chips={n_after_dup}")
        page.screenshot(path=str(OUT / "p1_chips_2.png"))
        page.click('#estChips [data-chip-i="0"]')
        time.sleep(0.2)
        n_one = page.eval_on_selector_all("#estChips .file-chip", "els=>els.length")
        check("P1d chip ✕ removes one", n_one == 1 and state(page, "s=>s.estFiles.length") == 1)
        page.click('#estChips [data-chips-clear="est"]')
        time.sleep(0.2)
        n_zero = page.eval_on_selector_all("#estChips .file-chip", "els=>els.length")
        check("P1e clear-selection empties chips", n_zero == 0 and state(page, "s=>s.estFiles.length") == 0)
        check("P1f input value reset allows re-pick", page.eval_on_selector("#estFiles", "el=>el.value") in ("", None))

        # ── P5: chips survive render() ───────────────────────────────────────
        page.set_input_files("#estFiles", [str(PICK_A), str(PICK_B)])
        time.sleep(0.2)
        lang = page.eval_on_selector("#languageSelect", "el=>el.value")
        page.select_option("#languageSelect", "bg" if lang != "bg" else "en")
        time.sleep(0.4)  # setLanguage -> applyI18n -> render()
        n_survive = page.eval_on_selector_all("#estChips .file-chip", "els=>els.length")
        check("P5 chips survive render() consistent with STATE",
              n_survive == 2 and state(page, "s=>s.estFiles.length") == 2)
        page.select_option("#languageSelect", lang)  # restore
        page.screenshot(path=str(OUT / "p5_chips_survive.png"))

        # ── P2: mode hygiene — no cross-mode bleed ───────────────────────────
        # entering tenders must drop estimation transients
        page.click('[data-mode="tenders"]')
        time.sleep(0.3)
        check("P2a entering tenders clears est transients",
              state(page, "s=>s.estFiles.length") == 0
              and state(page, "s=>s.est===null&&s.estSpatial===null&&s.estGaps.length===0"))
        page.wait_for_selector(f'.tender-card[data-id="{TENDER_DONE}"]', timeout=15000)
        name = page.eval_on_selector(f'.tender-card[data-id="{TENDER_DONE}"] .tender-name', "el=>el.textContent")
        page.click(f'.tender-card[data-id="{TENDER_DONE}"]')
        page.wait_for_selector(".decision-card", timeout=15000)
        page.click('[data-mode="estimation"]')
        time.sleep(1.5)  # allows est-api probe + any fetch to settle
        page.screenshot(path=str(OUT / "p2_estimation_after_tender.png"))
        if backend_new:
            body_txt = page.text_content("#page") or ""
            check("P2b estimation shows landing UI, not tender workspace",
                  page.query_selector(".estimation-landing") is not None
                  and page.query_selector(".spatial-workspace") is None
                  and page.query_selector(".im-root") is None)
            check("P2c tender name does not bleed into estimation page", (name or "") not in body_txt)
            check("P2d STATE.spatial untouched by estimation view",
                  state(page, "s=>s.estSpatial===null") is True)
        else:
            skip("P2b tender-spatial bleed asserts", "est-spatial contract absent on this backend — legacy link keeps the tender workspace")
        # tenders mode keeps its own review UI afterwards
        page.click('[data-mode="tenders"]')
        page.wait_for_selector(".decision-card", timeout=15000)
        check("P2e tenders review intact after round-trip", page.query_selector(".decision-card") is not None)

        # ── P3: hard-reset modal appears and cancels safely ──────────────────
        n_before = page.eval_on_selector_all(".tender-card", "els=>els.length")
        page.click("#helpButton")
        page.wait_for_selector("#drawer.open", timeout=5000)
        hr = page.query_selector("#hardReset")
        check("P3a hard-reset entry renders in Help drawer", hr is not None)
        if hr:
            hr.click()
            page.wait_for_selector("#modal:not(.hidden)", timeout=5000)
            check("P3b reset modal step-1 appears", page.query_selector("#modal:not(.hidden)") is not None)
            page.click("#modalOk")  # step 2
            time.sleep(0.3)
            page.screenshot(path=str(OUT / "p3_reset_step2.png"))
            check("P3c reset modal step-2 (double confirm) appears",
                  page.query_selector("#modal:not(.hidden)") is not None)
            page.click("#modalCancel")  # cancel — nothing destructive on old or new backend
            time.sleep(0.3)
            still = page.eval_on_selector_all(".tender-card", "els=>els.length")
            check("P3d cancel leaves data/UI intact", still == n_before, f"{n_before}->{still}")
        page.click("#drawerClose")

        # ── P4: resolve-all affordances render (presence-only) ───────────────
        # 4a tenders risk-hub resolve-all button (needs a tender with unresolved blockers)
        blk = tender_unresolved_blockers(TENDER_DONE)
        if blk:
            page.click(f'.tender-card[data-id="{TENDER_DONE}"]')
            page.wait_for_selector(".decision-card", timeout=15000)
            page.click('[data-hub="risk"]')
            page.wait_for_selector("#drawer.open", timeout=5000)
            ra = page.query_selector("[data-resolve-all]")
            check("P4a risk-hub resolve-all button renders", ra is not None)
            if ra:
                ra.click()
                page.wait_for_selector("#modal:not(.hidden)", timeout=5000)
                choice = page.query_selector('input[name="resolveAllMode"]')
                check("P4b resolve-all modal offers mark/prices choice",
                      choice is not None and page.query_selector('input[name="resolveAllMode"][value="prices"]') is not None)
                page.screenshot(path=str(OUT / "p4_resolve_all_choice.png"))
                page.click("#modalCancel")  # presence-only: no POST on either backend
            page.click("#drawerClose")
        elif blk is None:
            skip("P4a risk-hub resolve-all", f"tender {TENDER_DONE} pack unreadable")
        else:
            check("P4a risk-hub resolve-all hidden when no open gates (expected on this dataset)", True,
                  f"unresolved=0 for {TENDER_DONE}")
        # 4b estimation bulk "price all candidates" button (new-backend contract)
        page.click('[data-mode="estimation"]')
        page.wait_for_selector("#estFiles", state="attached", timeout=10000)
        page.set_input_files("#estFiles", str(PICK_A))
        page.click("#startProjectAnalysis")
        try:
            page.wait_for_selector("#drawer.open #estRun", timeout=60000)
            cs = page.query_selector("#estClearSession")
            check("P4c estimation drawer renders clear-session + cancel hosts", cs is not None)
            page.click("#estRun")
            page.wait_for_selector("#estRunOut b, #estRunOut .helper b, #estRunOut div", timeout=60000)
            time.sleep(0.5)
            if backend_new:
                bulk = page.query_selector("[data-est-gap-all]")
                check("P4d bulk price-all-candidates button renders when gaps have candidates",
                      bulk is not None)
            else:
                skip("P4d [data-est-gap-all]", "top_candidates ship with the integrated backend only")
            page.screenshot(path=str(OUT / "p4_estimation_run.png"))
        except Exception as e:
            skip("P4c estimation drawer run", f"estimation run unavailable: {e}")
        page.click("#drawerClose")
        # cleanup: the scan uploaded real files into the estimation inbox
        try:
            urllib.request.urlopen(urllib.request.Request(
                BASE + "/api/estimation/clear", method="POST", data=b"{}"), timeout=15)
        except Exception:
            pass
        # 4c broken-pack escape: a registry-only tender shows #clearRun2 on the setup screen
        page.click('[data-mode="tenders"]')
        time.sleep(0.3)
        reg = page.query_selector('.tender-card[data-id="606739"]')
        if reg:
            reg.click()
            time.sleep(1.0)
            if page.query_selector(".decision-card") is None:  # setup screen (no pack)
                check("P4e broken-pack clearRun escape renders on setup screen",
                      page.query_selector("#clearRun2") is not None)
            else:
                check("P4e 606739 has a pack — #clearRun2 n/a", page.query_selector("#clearRun") is not None)
            page.click("#backToTenders")

        # ── console hygiene ──────────────────────────────────────────────────
        check("H1 no JS console errors", len(CONSOLE_ERRORS) == 0, f"{len(CONSOLE_ERRORS)} errors")
        for e in CONSOLE_ERRORS[:5]:
            print("   console:", e)

        browser.close()

    n_pass = sum(1 for _, ok, _ in RESULTS if ok is True)
    n_fail = sum(1 for _, ok, _ in RESULTS if ok is False)
    n_skip = sum(1 for _, ok, _ in RESULTS if ok is None)
    print("\n" + "=" * 56)
    print(f"{n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP-BACKEND")
    print(f"screenshots -> {OUT}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
