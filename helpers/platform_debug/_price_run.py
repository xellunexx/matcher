import json, urllib.request
from collections import Counter

BASE = "http://127.0.0.1:7100/api/v1"
BOQ = "57df5cfb-bb38-4fdf-9ca5-af355cf1c7c6"

def req(method, path, body=None, tok=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    data = json.dumps(body).encode() if body is not None else None
    return json.load(urllib.request.urlopen(r, data))

tok = req("POST", "/users/auth/login/",
          {"email": "xel@tenderops.io", "password": "xel12345"})["access_token"]

boq = req("GET", f"/boq/boqs/{BOQ}", tok=tok)
pid = boq["project_id"]

lines = []
for p in boq["positions"]:
    u = p.get("unit") or ""
    if u == "section" or not u:
        continue
    q = p.get("quantity")
    try:
        qv = float(q) if q not in (None, "") else None
    except (TypeError, ValueError):
        qv = None
    lines.append({
        "description": p.get("description") or "",
        "unit": u,
        "quantity": qv,
        "source_ref": "",
    })
print("matchable lines:", len(lines))

CHUNK = 500
run_ids = []
for i in range(0, len(lines), CHUNK):
    part = lines[i:i + CHUNK]
    run = req("POST", "/cost-match/runs/", tok=tok, body={
        "project_id": pid,
        "name": f"KSS Hisarya BG corpus {i}-{i+len(part)}",
        "source_label": "KSS - Хисаря (BOQ 57df5cfb)",
        "source_locale": "bg",
        "cost_source": "all",
        "region": "BG_SOFIA",
        "candidate_limit": 200,
        "lines": part,
    })
    run_ids.append(run["id"])
    print(f"run {i}-{i+len(part)}: {run.get('counts')}")

tiers = Counter()
samples = {}
for rid in run_ids:
    off = 0
    while True:
        page = req("GET", f"/cost-match/runs/{rid}/results?limit=500&offset={off}", tok=tok)
        for r in page["items"]:
            tiers[r.get("tier", "?")] += 1
            t = r.get("tier", "?")
            if len(samples.setdefault(t, [])) < 3:
                samples[t].append(r)
        if off + len(page["items"]) >= page["total"]:
            break
        off += len(page["items"])

print("\nTIER TALLY:", dict(tiers))
total = sum(tiers.values())
matched = total - tiers.get("unmatched", 0)
print(f"resolved: {matched}/{total} = {matched*100//max(total,1)}%")
for t, rs in samples.items():
    print(f"\n--- tier {t} ---")
    for r in rs:
        print(" SRC:", (r.get("source_description") or "")[:80])
        print("  ->", r.get("suggested_code"), "|",
              (r.get("suggested_description") or "")[:70], "|",
              r.get("suggested_rate"), r.get("suggested_currency"), "| conf:", r.get("confidence"))
