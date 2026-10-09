import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

# corpus items with bulk units
cur.execute("""select unit, count(*), count(*) filter (where currency='EUR')
               from oe_costs_item where is_active and unit ~ '^[0-9]+\\s' group by 1 order by 2 desc limit 10""")
print("bulk-unit corpus items:", *cur.fetchall(), sep="\n  ")

# positions where a bulk-unit rate was actually applied (EUR)
cur.execute("""select p.ordinal, left(p.description,55), p.unit pos_unit, p.unit_rate,
                      p.metadata->'cost_match'->>'unit' sug_unit,
                      p.metadata->'cost_match'->>'currency' cur
               from oe_boq_position p
               where p.boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0'
                 and p.metadata->'cost_match'->>'unit' ~ '^[0-9]+\\s'
               order by p.ordinal limit 15""")
rows = cur.fetchall()
print("\nbulk-unit applied to positions:", len(rows))
for r in rows:
    print("  ", r)
