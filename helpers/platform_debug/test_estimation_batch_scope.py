# -*- coding: utf-8 -*-
"""Estimation batch-scope regression (2026-09-09):
every run must consume ONLY the batch uploaded since the previous run.

(a) upload batch A -> run -> pack A holds A's rows only; inbox root has no raw
    uploads left; runs/<id_A>/ holds A's file (inert archive)
(b) upload a DIFFERENT batch B -> run -> pack B holds B's rows ONLY, total differs
    (pre-fix behavior: every run re-parsed the whole inbox, so B's pack was
    poisoned by A's rows and totals were identical across runs)
(c) GET /api/estimation/history lists both packs, newest first
(d) manifest.json never carries entries for archived files and no dupes
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# isolate everything BEFORE importing the server (its module constants read env once)
tmp = Path(tempfile.mkdtemp(prefix="est_batch_"))
os.environ["TENDEROPS_BASE"] = str(tmp)
os.environ["TENDEROPS_WRITEBASE"] = str(tmp)
os.environ["TENDEROPS_DB_PATH"] = str(tmp / "tenderops.sqlite3")

from app import costdb, server as srv  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


def make_kss(path, prefix, rows):
    """Synthetic priced КСС: header + rows -> parse_kss_priced yields own_price rows."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "КСС"
    ws.append(["№", "Описание на СМР", "Ед. мярка", "Количество", "Ед. цена"])
    for i, (desc, unit, qty, price) in enumerate(rows, 1):
        ws.append([i, f"{prefix} :: {desc}", unit, qty, price])
    wb.save(path)


ROWS_A = [("Направа на изкоп за ивичен фундамент", "м3", 10.0, 12.5),
          ("Доставка и полагане на подложен бетон C12/15", "м3", 4.0, 100.0)]
ROWS_B = [("Демонтаж на стари дървени дограми с доизкупуване", "бр", 2.0, 35.0),
          ("Мазилка вароциментова по стени дебелина 20 мм", "м2", 21.0, 8.0),
          ("Полагане на гранитогресни настилки 60х60", "м2", 7.0, 25.0)]
EXP_A = round(sum(q * p for _, _, q, p in ROWS_A), 2)
EXP_B = round(sum(q * p for _, _, q, p in ROWS_B), 2)

httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.H)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def call(path, method="GET", body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"Content-Type": ctype}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def upload(path, rows, prefix):
    make_kss(path, prefix, rows)
    code, j = call(f"/api/estimation/scan?filename={urllib.parse.quote(path.name)}",
                   "POST", raw=path.read_bytes(), ctype="application/octet-stream")
    assert code == 200 and j.get("ok"), f"upload failed: {code} {j}"
    return j["saved"]


try:
    costdb.connect(tmp / "tenderops.sqlite3").close()  # schema only; no corpus needed (all rows OBSERVED)
    inbox = srv.DEMO / "estimation_inbox"

    # --- (a) batch A ---
    saved_a = upload(tmp / "batch_a_src.xlsx", ROWS_A, "Партида А")
    code, run_a = call("/api/estimation/run", "POST", {"files": [saved_a]})
    check("a: run A ok", code == 200 and run_a.get("ok"), f"{code}")
    check("a: row count", run_a.get("rows") == len(ROWS_A), str(run_a.get("rows")))
    check("a: total", abs(run_a.get("totalExclVat", -1) - EXP_A) < 0.005, str(run_a.get("totalExclVat")))
    check("a: xlsx written (archive precondition)", bool(run_a.get("kssXlsx")), str(run_a.get("kssXlsx")))
    id_a = run_a.get("estimationId") or ""
    pack_a = json.loads((inbox / f"estimation_{id_a}.json").read_text(encoding="utf-8-sig")) if id_a else {}
    descs_a = [l["desc"] for l in pack_a.get("boq") or []]
    check("a: pack holds A rows only", len(descs_a) == len(ROWS_A) and
          all(d.startswith("Партида А") for d in descs_a), json.dumps(descs_a, ensure_ascii=False)[:160])
    leftovers = [p.name for p in inbox.glob("*") if p.is_file() and not srv._is_estimation_artifact(p.name)]
    check("a: inbox root has no raw uploads left", leftovers == [],
          json.dumps(leftovers, ensure_ascii=False))
    arch_a = inbox / "runs" / id_a
    check("a: runs/<id_A> holds A's file", (arch_a / saved_a).is_file(),
          f"{list(arch_a.glob('*')) if arch_a.exists() else 'no dir'}")

    # --- manifest after run A: deduped, no archived entries ---
    code, scan = call("/api/estimation/scan")
    files_m = (scan.get("files") or [])
    saveds = [r.get("saved") for r in files_m]
    check("d: manifest empty after run A (A archived)", files_m == [], json.dumps(saveds, ensure_ascii=False))

    # --- (b) batch B ---
    saved_b = upload(tmp / "batch_b_src.xlsx", ROWS_B, "Партида Б")
    code, scan = call("/api/estimation/scan")
    saveds = [r.get("saved") for r in (scan.get("files") or [])]
    check("d: manifest = B only, no dupes", saveds == [saved_b], json.dumps(saveds, ensure_ascii=False))
    code, run_b = call("/api/estimation/run", "POST", {"files": [saved_b]})
    check("b: run B ok", code == 200 and run_b.get("ok"), f"{code}")
    check("b: row count = B only", run_b.get("rows") == len(ROWS_B), str(run_b.get("rows")))
    check("b: total = B and differs from A", abs(run_b.get("totalExclVat", -1) - EXP_B) < 0.005
          and abs(EXP_B - EXP_A) > 0.005, f"B={run_b.get('totalExclVat')} A={EXP_A}")
    id_b = run_b.get("estimationId") or ""
    pack_b = json.loads((inbox / f"estimation_{id_b}.json").read_text(encoding="utf-8-sig")) if id_b else {}
    descs_b = [l["desc"] for l in pack_b.get("boq") or []]
    check("b: pack B has B rows only (no A poisoning)", len(descs_b) == len(ROWS_B) and
          all(d.startswith("Партида Б") for d in descs_b), json.dumps(descs_b, ensure_ascii=False)[:200])
    check("b: runs/<id_B> holds B's file", (inbox / "runs" / id_b / saved_b).is_file())

    # --- (c) history ---
    code, hist = call("/api/estimation/history")
    hist = hist.get("history") or []
    ids = [h.get("id") for h in hist]
    check("c: history ok", code == 200, f"{code}")
    check("c: history lists both ids", id_a in ids and id_b in ids, json.dumps(ids, ensure_ascii=False))
    check("c: newest first", ids[:2] == [id_b, id_a], json.dumps(ids, ensure_ascii=False))
    if hist:
        first = next(h for h in hist if h["id"] == id_b)
        check("c: B entry carries rows+total", first["rows"] == len(ROWS_B) and
              abs((first["totalExclVat"] or 0) - EXP_B) < 0.005, json.dumps(first, ensure_ascii=False))

    # --- clear keeps history, drops un-run uploads ---
    code, run_c = call("/api/estimation/clear", "POST", {})
    code, hist2 = call("/api/estimation/history")
    code_a_pack = (inbox / f"estimation_{id_a}.json").exists()
    check("clear: packs survive", code_a_pack and (inbox / f"estimation_{id_b}.json").exists()
          and (inbox / "runs" / id_a / saved_a).exists())
    check("clear: history intact", [h["id"] for h in (hist2.get("history") or [])][:2] == [id_b, id_a])
finally:
    httpd.shutdown()
    httpd.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
