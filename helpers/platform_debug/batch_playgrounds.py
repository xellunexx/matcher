# -*- coding: utf-8 -*-
"""Acquire + process the 5 playground tenders via the running exe, then summarize."""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8077"
IDS = [606060, 550561, 560538, 605951, 603210]

def post(path, payload=None, timeout=900):
    b = json.dumps(payload or {}).encode()
    req = urllib.request.Request(BASE + path, data=b, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}

out = []
for tid in IDS:
    row = {"id": tid}
    st, r = post("/api/tenders/add", {"ref": str(tid)}, timeout=180)
    if not r.get("ok"):
        row["error"] = f"add {st}: {r.get('error')}"
        out.append(row)
        continue
    row["name"] = (r["tender"].get("name") or "")[:70]
    row["docs"] = len(r["tender"].get("documents") or [])
    row["deadline"] = r["tender"].get("deadline")
    row["estValue"] = r["tender"].get("estValue")
    st, r = post(f"/api/tender/{tid}/process", {}, timeout=900)
    if not r.get("ok"):
        row["error"] = f"process {st}: {r.get('error')}"
        out.append(row)
        continue
    row["rows"] = r["rows"]
    row["priced"] = r["priced"]
    row["totalExclVat"] = r["totalExclVat"]
    row["kss"] = r["kss"]
    row["llm"] = r["llmOk"]
    pack = json.load(open(f"dist/data/demo/processed/{tid}.json", encoding="utf-8-sig"))
    row["est"] = [{"no": l["no"], "desc": l["desc"][:60], "unit": l["unit"], "qty": l["qty"]}
                  for l in pack["boq"] if l["rule"] == "EST"][:8]
    out.append(row)
    print(f"== {tid} done: {r['rows']} rows, {r['priced']} priced, {r['totalExclVat']} EUR", flush=True)

print("FINAL_JSON:" + json.dumps(out, ensure_ascii=False))
