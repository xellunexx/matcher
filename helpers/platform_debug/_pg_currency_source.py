import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select source, currency, count(*)
               from oe_costs_item
               where is_active
               group by source, currency
               order by count(*) desc""")
for r in cur.fetchall():
    print(r)
