import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
BOQ = "3824c126-e054-4b65-aa51-16ddc493d821"
cur.execute("""select price_basis, count(*),
                      count(*) filter (where unit_rate::numeric>0) priced,
                      coalesce(sum(total::numeric),0)
               from oe_boq_position where boq_id=%s group by price_basis""", (BOQ,))
print("positions by basis:", cur.fetchall())
cur.execute("""select id, status, created_at, item_count, left(notes::text,150)
               from oe_cost_match_run order by created_at desc limit 4""")
print("latest runs:")
for r in cur.fetchall(): print(" ", r)
cur.execute("""select ordinal, left(description,45), unit, unit_rate, price_basis
               from oe_boq_position where boq_id=%s
               and unit_rate::numeric>0 order by total::numeric desc limit 8""", (BOQ,))
print("top priced:")
for r in cur.fetchall(): print(" ", r)
