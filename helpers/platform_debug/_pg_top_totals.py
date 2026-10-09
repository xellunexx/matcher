import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select p.ordinal, left(p.description,50), p.unit, p.quantity, p.unit_rate, p.total,
                      p.price_basis, p.metadata->'cost_match'->>'unit' sug_unit,
                      p.metadata->'cost_match'->>'suggested_rate' sug_rate,
                      p.metadata->'cost_match'->>'currency' cur,
                      p.metadata->'cost_match'->>'unit_scale' uscale
               from oe_boq_position p
               where p.boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0'
               order by p.total::numeric desc nulls last limit 15""")
for r in cur.fetchall():
    print([str(x)[:60] for x in r])
