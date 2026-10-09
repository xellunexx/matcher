import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select coalesce(sum(total::numeric),0),
                      count(*) filter (where unit_rate::numeric>0),
                      count(*) filter (where unit_rate::numeric=0)
               from oe_boq_position
               where boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0'
               and coalesce(node_type,'position') != 'section'""")
print("BOQ total:", cur.fetchall())
cur.execute("""select price_basis, count(*) from oe_boq_position
               where boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0'
               group by price_basis order by 2 desc""")
print(cur.fetchall())
# verify the rebar/kg lines that inflated before
cur.execute("""select left(description,40), unit, unit_rate, total,
                      metadata->'cost_match'->>'unit' sug_unit
               from oe_boq_position
               where boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0'
               and description ilike '%Армировка%ф6.5%' limit 4""")
for r in cur.fetchall(): print(r)
