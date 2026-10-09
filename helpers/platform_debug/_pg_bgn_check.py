import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select count(*) from oe_costs_item
               where currency='BGN' and (rate is null or rate !~ '^[0-9]+(\\.[0-9]+)?$')""")
print("non-numeric BGN rates:", cur.fetchall())
cur.execute("""select rate, count(*) from oe_costs_item where currency='BGN'
               and rate !~ '^[0-9]+(\\.[0-9]+)?$' group by rate limit 10""")
print(cur.fetchall())
cur.execute("select metadata from oe_costs_item where currency='BGN' and metadata is not null limit 2")
for r in cur.fetchall(): print(r)
