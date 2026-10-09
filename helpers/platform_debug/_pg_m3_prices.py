import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select left(description,70), unit, rate, currency, source from oe_costs_item
               where rate::numeric > 150 and rate::numeric < 260 and (unit ilike 'M3%' or unit ilike 'м3%')
               order by rate::numeric limit 20""")
for r in cur.fetchall():
    print(r)
print("\n-- rate exactly 200.37:")
cur.execute("select left(description,70), unit, rate, currency, source from oe_costs_item where rate::numeric=200.37 limit 10")
for r in cur.fetchall():
    print(r)
