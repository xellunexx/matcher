import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=52465, user="postgres", dbname="postgres", connect_timeout=8)
cur = c.cursor()

cur.execute("select count(*), count(*) filter (where is_active) from oe_costs_item")
total, active = cur.fetchone()
print(f"oe_costs_item: total={total} active={active}")

cur.execute("""select coalesce(source,'?'), count(*),
               count(*) filter (where is_active) as act
               from oe_costs_item group by source order by 2 desc limit 15""")
print("\n-- by source:")
for r in cur.fetchall(): print("  ", r)

print("\n-- the two flagged codes:")
for pat in ('%БЛ21.215%', '%21.215%'):
    cur.execute("""select code, unit, rate, currency, source, is_active, left(description,80)
                   from oe_costs_item where code like %s limit 3""", (pat,))
    for r in cur.fetchall(): print("  ", r)

cur.execute("""select code, unit, rate, currency, source, is_active, left(description,80)
               from oe_costs_item where rate between 4856 and 4858 limit 5""")
print("\n-- 4856.85 rows:")
for r in cur.fetchall(): print("  ", r)

cur.close(); c.close()

