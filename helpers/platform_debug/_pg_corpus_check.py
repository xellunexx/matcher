import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

# is the real demolition price in ERP corpus?
cur.execute("""select description, unit, rate, currency, source, is_active
               from oe_costs_item where description like '%багер-чук%' order by rate""")
for r in cur.fetchall():
    print(r)

# the 21 manual_entry poison rows
print("\n-- manual_entry rows:")
cur.execute("""select left(description,60), unit, rate, currency, source
               from oe_costs_item where source='manual_entry' order by created_at""")
for r in cur.fetchall():
    print(r)
