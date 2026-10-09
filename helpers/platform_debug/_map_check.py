import json, sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open(r"C:\lab\tenderops\platform\_bill_terms_dump.json", encoding="utf-8"))
print("webanchors rows:", {k: d[k] for k in d if k.startswith("webanchors")})
c = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
codes = {r[0] for r in c.execute("select distinct code from cost_items where code is not null and code!=''")}
hit, missed = 0, []
for k in d:
    short = k.split("-", 2)[-1] if k.startswith("BUILDLY-") else k
    if short in codes:
        hit += 1
    else:
        missed.append(k)
print("mapped:", hit, "unmapped:", len(missed))
print("missed:", missed[:40])
# webanchors ids in tos?
ids = {r[0] for r in c.execute("select id from cost_items where source_key='webanchors'")}
wh = [k for k in d if k.startswith("webanchors") and k in ids]
print("webanchor-id matches:", len(wh), wh)
