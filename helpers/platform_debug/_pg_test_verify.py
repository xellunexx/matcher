import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
BOQ = "3824c126-e054-4b65-aa51-16ddc493d821"
cur.execute("""select price_basis, count(*),
                      count(*) filter (where unit_rate::numeric>0) priced,
                      coalesce(sum(total::numeric),0)
               from oe_boq_position where boq_id=%s group by price_basis order by 2 desc""", (BOQ,))
print("basis:", cur.fetchall())
cur.execute("""select ordinal, left(description,45), unit, unit_rate, price_basis,
                      metadata->'cost_match'->>'description' sug_desc,
                      metadata->'cost_match'->>'unit' sug_unit,
                      metadata->'cost_match'->>'suggested_rate' sug_rate
               from oe_boq_position where boq_id=%s and ordinal in ('1','2','3','4','43.3')""", (BOQ,))
print("\nkey rows:")
for r in cur.fetchall(): print(" ", r)
