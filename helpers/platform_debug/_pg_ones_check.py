import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select p.ordinal, left(p.description,60), p.unit, p.unit_rate, p.price_basis,
                      p.metadata->'cost_match'->>'description' src, p.metadata->'cost_match'->>'fx' fx
               from oe_boq_position p
               where p.boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0' and p.unit_rate::numeric=1.0""")
for r in cur.fetchall():
    print(r)

# total sanity
cur.execute("""select sum(total::numeric) from oe_boq_position
               where boq_id='5726cd49-1430-41ff-8ff7-a69886e0f3c0'""")
print("\nBOQ total:", cur.fetchone()[0])
