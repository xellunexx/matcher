# -*- coding: utf-8 -*-
"""Live proof: Buildly pending_review hints appear on playground EST rows."""
import json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

def post(path, payload, t=900):
    b = json.dumps(payload).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8077" + path, data=b,
                               headers={"Content-Type": "application/json"}), timeout=t)
    return json.loads(r.read())

r = post("/api/tender/606060/process", {"force": True})
print("process:", json.dumps(r, ensure_ascii=False))
pk = json.load(open(r"dist\data\demo\processed\606060.json", encoding="utf-8-sig"))
ests = [l for l in pk["boq"] if l["rule"] == "EST"]
hinted = [l for l in ests if "анкер" in (l.get("note") or "") or "референт" in (l.get("note") or "")]
print(f"EST rows: {len(ests)} | with hints: {len(hinted)}")
for l in hinted[:5]:
    print("  -", l["desc"][:54], "||", (l.get("note") or "")[-110:])
