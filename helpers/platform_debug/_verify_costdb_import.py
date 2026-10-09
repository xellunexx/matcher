"""Verify the TenderOps -> ERP cost import on staging (PG :50554, API :7100)."""
import json
import sys
import urllib.request
import urllib.error

import psycopg2

IO = {"encoding": "utf-8"}
sys.stdout.reconfigure(**IO)

c = psycopg2.connect(host="127.0.0.1", port=50554, user="postgres", dbname="postgres")
cur = c.cursor()

def q(sql):
    cur.execute(sql)
    return cur.fetchall()

print("== SQL state ==")
print("tenderops rows:", q("SELECT count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id'")[0][0])
print("active/inactive:",
      q("SELECT is_active, count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' GROUP BY 1"))
print("by tenderops_status:",
      q("SELECT metadata->>'tenderops_status', is_active, count(*) FROM oe_costs_item "
        "WHERE metadata ? 'tenderops_id' GROUP BY 1,2 ORDER BY 1"))
print("regions:", q("SELECT region, count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' GROUP BY 1"))
print("currency:", q("SELECT currency, count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' GROUP BY 1"))
print("catalogs:", q("SELECT count(*) FROM oe_costs_catalog"))
print("items w/o catalog:", q("SELECT count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND catalog_id IS NULL")[0][0])
print("price_as_of set:", q("SELECT count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND price_as_of IS NOT NULL")[0][0])
print("dup (code,region):", q("SELECT count(*) FROM (SELECT code, region FROM oe_costs_item "
                            "WHERE metadata ? 'tenderops_id' GROUP BY 1,2 HAVING count(*)>1) t")[0][0])
print("rate<=0:", q("SELECT count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND rate::numeric <= 0")[0][0])
print("empty description:", q("SELECT count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND btrim(description)=''")[0][0])
print("non-EUR rows:", q("SELECT code, rate, currency FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND currency<>'EUR' LIMIT 5"))
print("sample unicode:", q("SELECT code, left(description,70), unit, rate FROM oe_costs_item "
                          "WHERE metadata ? 'tenderops_id' AND description ~ '[^\\x00-\\x7F]' LIMIT 3"))
print("provenance sample:", q("SELECT code, metadata->>'origin_kind', metadata->>'tenderops_source_key', "
                            "left(metadata->>'origin_ref',40) FROM oe_costs_item "
                            "WHERE metadata ? 'tenderops_id' LIMIT 3"))
print("tags with GOVSCR:", q("SELECT count(*) FROM oe_costs_item WHERE metadata ? 'tenderops_code'")[0][0])

print("\n== API visibility ==")
login = urllib.request.Request(
    "http://127.0.0.1:7100/api/v1/users/auth/login/",
    data=json.dumps({"email": "xel@tenderops.io", "password": "xel12345"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
token = json.loads(urllib.request.urlopen(login).read())["access_token"]

def api(path):
    r = urllib.request.Request("http://127.0.0.1:7100" + path, headers={"Authorization": f"Bearer {token}"})
    try:
        return json.loads(urllib.request.urlopen(r).read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read()[:300].decode("utf-8", "replace")}

cats = api("/api/v1/costs/catalogs/")
print("catalogs via API:", len(cats), "| total items:", sum(c.get("item_count", 0) for c in cats))

# Pick one active and one pending item and probe autocomplete.
active_code = q("SELECT code, description FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND is_active LIMIT 1")[0]
pending_code = q("SELECT code, description FROM oe_costs_item WHERE metadata ? 'tenderops_id' AND NOT is_active LIMIT 1")[0]
print("probe active:", active_code)
print("probe pending:", pending_code)

for label, (code, desc) in (("ACTIVE", active_code), ("PENDING", pending_code)):
    term = code
    res = api("/api/v1/costs/autocomplete/?q=" + urllib.request.quote(term) + "&limit=10")
    hits = [x for x in res if isinstance(x, dict) and x.get("code") == code]
    print(f"  {label} code '{code}' in autocomplete: {len(hits)} hit(s)")

# search endpoint visibility too
for label, (code, desc) in (("ACTIVE", active_code), ("PENDING", pending_code)):
    res = api("/api/v1/costs/?limit=5&q=" + urllib.request.quote(code))
    print(f"  {label} list-search '{code}':", res if isinstance(res, dict) and "_error" in res else
          [x.get("code") for x in (res.get("items", res) if isinstance(res, dict) else res)][:5])
