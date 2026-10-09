import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select column_name, data_type from information_schema.columns
               where table_name='oe_costs_item' order by ordinal_position""")
for r in cur.fetchall():
    print(r)
cur.execute("""select currency, count(*) from oe_costs_item
               where is_active and currency='BGN' group by currency""")
print("BGN rows:", cur.fetchall())
# also non-active
cur.execute("select currency, count(*) from oe_costs_item where not is_active and currency='BGN' group by currency")
print("inactive BGN:", cur.fetchall())
