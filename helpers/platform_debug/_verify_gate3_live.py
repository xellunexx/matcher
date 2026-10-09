# -*- coding: utf-8 -*-
"""Live Gate-3 verification: cached short-circuit + rerun idempotency on the exe."""
import json, sqlite3, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

def post(path, payload, t=900):
    b = json.dumps(payload).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8077" + path, data=b,
                               headers={"Content-Type": "application/json"}), timeout=t)
    return json.loads(r.read())

r1 = post("/api/tender/605862/process", {})             # cached expected (same env)
print("call 1:", json.dumps(r1, ensure_ascii=False))
r2 = post("/api/tender/605862/process", {})             # must hit cache
print("call 2 cached:", r2.get("cached"), "| env:", r2.get("env"))
r3 = post("/api/tender/605862/process", {"force": True})  # force reruns deterministically
print("call 3 force ok:", r3.get("ok"), "| cached:", r3.get("cached"))
con = sqlite3.connect(r"dist\tenderops.sqlite3")
print("matches for 605862:", con.execute("SELECT COUNT(*) FROM matches WHERE tender_id=605862").fetchone()[0])
print("fingerprints:", con.execute("SELECT COUNT(*) FROM tender_fingerprints WHERE tender_id=605862").fetchone()[0])
con.close()
