# -*- coding: utf-8 -*-
"""FACTS items 3-5 probe: explicit input selection for /api/estimation/run.

Server-backed. RE-USES archived evidence batches only (no new uploads, no DB
cross-contamination beyond timestamped test artifacts).

1. POST with no files            -> 400 (validator)
2. POST with a bogus/POC name    -> 400 (validator blocks traversal/garbage)
3. Rerun archived batch A (same files, sourceRun=A) -> SAME total as A's pack
4. Rerun archived batch B (different content)        -> B's own total, != A's
5. sourceRun archives are NOT drained (copy, not rename)
6. pack records inputFiles exactly as selected
"""
import json, os, sys, time, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8123")
INBOX = "data/demo/estimation_inbox"

def http(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace"))

def pack_of(est_id):
    p = os.path.join(INBOX, "estimation_" + est_id + ".json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    return None

def files_of(run_id):
    d = os.path.join(INBOX, "runs", run_id)
    return sorted(os.listdir(d)) if os.path.isdir(d) else None

out = {"ok": True}
try:
    # archive A: EST20260912_031225 (4 files), B: EST20260912_020048? -> has 69275.12
    hist = http("/api/estimation/history")["history"]
    tot = {h["id"]: h["totalExclVat"] for h in hist}
    A = "EST20260912_031225"
    B = "EST20260912_021021"      # 69275.12 — different content pack
    fA, fB = files_of(A), files_of(B)
    assert fA and fB, "archives missing"
    out["A"] = {"files": len(fA), "total": tot.get(A)}
    out["B"] = {"files": len(fB), "total": tot.get(B)}

    # 1. no files -> 400
    st, j = post("/api/estimation/run", {})
    out["no_files"] = {"status": st, "ok": st == 400 and bool(j.get("error"))}

    # 2. bogus name -> 400
    st, j = post("/api/estimation/run", {"files": ["../server.py"]})
    out["traversal"] = {"status": st, "ok": st == 400}
    st, j = post("/api/estimation/run", {"files": ["nope_no_file.png.exe"]})
    out["bogus"] = {"status": st, "ok": st == 400}

    # 3. rerun A from archive, same files
    st, j = post("/api/estimation/run", {"files": fA, "sourceRun": A})
    out["rerunA"] = {"status": st, "rows": j.get("rows"), "total": j.get("totalExclVat"),
                     "same_total": st == 200 and abs((j.get("totalExclVat") or 0) - tot[A]) < 0.05,
                     "inputFiles": (pack_of(j.get("estimationId", "")) or {}).get("inputFiles")}

    # 4. rerun B from archive -> OWN total, different from A
    st, j = post("/api/estimation/run", {"files": fB, "sourceRun": B})
    out["rerunB"] = {"status": st, "rows": j.get("rows"), "total": j.get("totalExclVat"),
                     "own_total": st == 200 and abs((j.get("totalExclVat") or 0) - tot[B]) < 0.05,
                     "differs_from_A": st == 200 and abs((j.get("totalExclVat") or 0) - tot[A]) > 1.0}

    # 5. source archives never drained
    out["archives_intact"] = os.path.isdir(os.path.join(INBOX, "runs", A)) and os.path.isdir(os.path.join(INBOX, "runs", B)) \
        and set(os.listdir(os.path.join(INBOX, "runs", A))) == set(fA) \
        and set(os.listdir(os.path.join(INBOX, "runs", B))) == set(fB)

    ok = (out["no_files"]["ok"] and out["traversal"]["ok"] and out["bogus"]["ok"]
          and out["rerunA"]["same_total"]
          and out["rerunA"]["inputFiles"] == fA
          and out["rerunB"]["own_total"] and out["rerunB"]["differs_from_A"]
          and out["archives_intact"])
    out["ok"] = bool(ok)
except Exception as ex:
    out["ok"] = False
    out["error"] = repr(ex)
print(json.dumps(out, ensure_ascii=False, indent=1))
sys.exit(0 if out["ok"] else 1)
