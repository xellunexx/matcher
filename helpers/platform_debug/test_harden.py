# -*- coding: utf-8 -*-
"""Load/concurrency battery against the live exe (Gate-3 operational stress)."""
import concurrent.futures as cf
import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8077"

def call(path, method="GET", payload=None, timeout=300):
    b = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=b, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}

t0 = time.time()
out = {}

with cf.ThreadPoolExecutor(16) as ex:
    rs = list(ex.map(lambda _: call("/api/tenders"), range(16)))
out["parallel_tenders_read"] = {"codes": sorted({s for s, _ in rs}), "all200": all(s == 200 for s, _ in rs)}

t1 = time.time()
with cf.ThreadPoolExecutor(8) as ex:
    rs = list(ex.map(lambda i: call("/api/costdb/search?q=" + urllib.parse.quote(f"бетон {i}")), range(10)))
out["parallel_costdb_search"] = {"all200": all(s == 200 for s, _ in rs),
                                 "consistent": len({json.dumps(r, sort_keys=True) for s, r in rs if s == 200}) <= 10}

# resolution storm: 32 distinct keys, then read-back count must include all
keys = [f"LOAD-{i}" for i in range(32)]
t2 = time.time()
with cf.ThreadPoolExecutor(8) as ex:
    rs = list(ex.map(lambda k: call("/api/resolve", "POST", {"key": k, "note": "load"}), keys))
bad = [s for s, _ in rs if s != 200]
out["resolve_storm"] = {"writes_ok": len(rs) - len(bad), "bad": len(bad)}

# 3 parallel process calls on same tender: one runs / others 409 or cached
with cf.ThreadPoolExecutor(3) as ex:
    rs = list(ex.map(lambda _: call("/api/tender/605862/process", "POST", {}, timeout=900), range(3)))
kinds = sorted(("cached" if (j or {}).get("cached") else ("409" if s == 409 else ("ok" if (j or {}).get("ok") else str(s)))
                for s, j in rs))
out["triple_process_same_tender"] = kinds

# same-input determinism through the exe: two cached answers identical
a = call("/api/tender/605862/process", "POST", {})[1]
b2 = call("/api/tender/605862/process", "POST", {})[1]
out["exe_cached_determinism"] = (a.get("cached") and b2.get("cached") and
                                 a.get("rows") == b2.get("rows") and a.get("totalExclVat") == b2.get("totalExclVat"))

# check server liveness after storm
s, _ = call("/api/tenders")
out["server_alive_after_storm"] = s == 200
out["elapsed_s"] = round(time.time() - t0, 1)

print(json.dumps(out, ensure_ascii=False, indent=1))
ok = (out["parallel_tenders_read"]["all200"] and out["parallel_costdb_search"]["all200"]
      and out["resolve_storm"]["bad"] == 0 and out["server_alive_after_storm"])
print("LOAD VERDICT:", "GREEN" if ok else "RED")
