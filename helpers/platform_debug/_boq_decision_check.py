import json, urllib.request

BASE = "http://127.0.0.1:7100/api/v1"
BOQ = "57df5cfb-bb38-4fdf-9ca5-af355cf1c7c6"
RUN = "92e9c386-6b12-4132-b4f3-6b64bafdbf02"

def req(method, path, body=None, tok=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    data = json.dumps(body).encode() if body is not None else None
    return json.load(urllib.request.urlopen(r, data))

tok = req("POST", "/users/auth/login/",
          {"email": "xel@tenderops.io", "password": "xel12345"})["access_token"]

results = req("GET", f"/cost-match/runs/{RUN}/results?limit=5", tok=tok)
res = results["items"][0]
print("result:", res["line_no"], res["tier"], res["suggested_code"], res["suggested_rate"])

dec = req("POST", f"/cost-match/results/{res['id']}/decision", tok=tok,
          body={"decision": "confirmed", "note": "e2e check"})
print("decision:", dec["decision"], dec["decided_code"], dec["decided_rate"])

boq = req("GET", f"/boq/boqs/{BOQ}", tok=tok)
for p in boq["positions"]:
    cm = (p.get("metadata") or {}).get("cost_match") or {}
    if cm.get("result_id") == res["id"]:
        print("position:", p["description"][:60])
        print("  unit_rate:", p["unit_rate"], "| basis:", p["price_basis"],
              "| state:", cm.get("state"), "| decision:", cm.get("decision"))
        break
