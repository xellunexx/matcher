# -*- coding: utf-8 -*-
"""Cleanup my test debris from the user's resolutions DB + process 588658."""
import json, sqlite3, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

con = sqlite3.connect(r"dist\tenderops.sqlite3")
before = con.execute("SELECT COUNT(*) FROM human_resolutions").fetchone()[0]
con.execute("DELETE FROM human_resolutions WHERE boq_key LIKE 'LOAD-%' OR boq_key IN ('R-CHk','R1')")
con.commit()
after = con.execute("SELECT COUNT(*) FROM human_resolutions").fetchone()[0]
con.close()
print(f"resolutions cleaned: {before} -> {after} (kept: user's real approvals)")

def post(path, payload, t=1800):
    b = json.dumps(payload).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8077" + path, data=b,
                               headers={"Content-Type": "application/json"}), timeout=t)
    return json.loads(r.read())

r = post("/api/tenders/add", {"ref": "588658"}, 300)
if r.get("ok"):
    t = r["tender"]
    print("ADDED:", t["id"], "|", (t.get("name") or "")[:76], "| docs:", len(t.get("documents") or []),
          "| cap:", t.get("estValue"), "| deadline:", t.get("deadline"))
    p = post("/api/tender/588658/process", {})
    print("PROCESS:", json.dumps(p, ensure_ascii=False))
else:
    print("ADD FAIL:", r)
