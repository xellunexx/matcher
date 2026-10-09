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
print("BOQ:", boq["name"], "| positions:", len(boq["positions"]),
      "| locked:", boq.get("is_locked"))

print("POST /cost-match/boq/{id}/run ...")
res = req("POST", f"/cost-match/boq/{BOQ}/run", tok=tok, body={
    "source_locale": "bg",
    "cost_source": "all",
    "region": "BG_SOFIA",
    "candidate_limit": 200,
})
print("RESPONSE:", json.dumps(res, indent=1))

boq2 = req("GET", f"/boq/boqs/{BOQ}", tok=tok)
tiers = Counter()
priced = 0
fx = 0
samples = []
for p in boq2["positions"]:
    cm = (p.get("metadata") or {}).get("cost_match")
    if not cm:
        continue
    tiers[cm.get("tier")] += 1
    if cm.get("currency_mismatch"):
        fx += 1
    try:
        if float(p.get("unit_rate") or 0) > 0:
            priced += 1
            if len(samples) < 8:
                samples.append((p["description"][:60], p["unit_rate"],
                                cm.get("code"), cm.get("tier")))
    except (TypeError, ValueError):
        pass

print("positions with cost_match meta:", sum(tiers.values()))
print("positions priced (rate>0):", priced, "| fx-flagged:", fx)
print("tier spread:", dict(tiers))
print("basis spread:", Counter(p.get("price_basis") for p in boq2["positions"] if p.get("price_basis")))
for s in samples:
    print("  *", s)
