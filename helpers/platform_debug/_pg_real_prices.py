import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

for pat, val in [("%Разбиване на бетон%", None), ("%Къртене бетон%", None), (None, 200.37), ("%демонтаж бетон%", None)]:
    if pat:
        cur.execute("select left(description,70), unit, rate, currency, source from oe_costs_item where description ilike %s order by rate limit 12", (pat,))
    else:
        cur.execute("select left(description,70), unit, rate, currency, source from oe_costs_item where rate=%s limit 12", (val,))
    print(f"\n== {pat or val}:")
    for r in cur.fetchall():
        print("  ", r)

# sample of the biggest sources - what does cwicr / won_or_filed_tender hold?
for src in ("won_or_filed_tender", "reference_web", "cwicr"):
    cur.execute("select left(description,60), unit, rate, currency from oe_costs_item where source=%s limit 4", (src,))
    print(f"\n== sample {src}:")
    for r in cur.fetchall():
        print("  ", r)
