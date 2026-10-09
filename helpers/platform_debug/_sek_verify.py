import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\lab\tenderops\tenderops\app")
import costdb

info = costdb.sync_json_sources(r"C:\lab\tenderops\tenderops\data\demo",
                                r"C:\lab\tenderops\tenderops\tenderops.sqlite3", force=False)
print("sync:", info if not isinstance(info, dict) else {k: v for k, v in info.items() if k != "sources"})

con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
con.row_factory = sqlite3.Row
rows = list(con.execute("SELECT code, desc, unit, amount_eur, status FROM cost_items WHERE code LIKE 'SEK-%' ORDER BY code LIMIT 8"))
for r in rows:
    print(dict(r))
print("sek count:", con.execute("SELECT COUNT(*) c FROM cost_items WHERE code LIKE 'SEK-%'").fetchone()["c"])
import re
lat = con.execute("SELECT COUNT(*) c FROM cost_items WHERE code LIKE 'SEK-%' AND desc GLOB '*[A-Za-z]*'").fetchone()["c"]
print("sek rows still containing latin chars:", lat)

# probe: match through the pipeline
import pipeline
best, score, ev = pipeline.match_cost_v2(
    {"desc": "Остъкляване с 3 мм стъкла в/у дървени рамки при ремонти", "unit": "м2"},
    r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
print("probe:", ev.get("method"), score, (best or {}).get("code"), (best or {}).get("amount_eur"))
