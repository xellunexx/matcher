import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

# the €1/PIECE filler rows - same set quarantined in the TenderOps corpus
cur.execute("""select count(*) from oe_costs_item
               where source='manual_entry' and rate::numeric=1.0 and unit='PIECE'""")
print("poison rows:", cur.fetchone()[0])

cur.execute("""update oe_costs_item set is_active=false, updated_at=now()
               where source='manual_entry' and rate::numeric=1.0 and unit='PIECE'""")
print("retired:", cur.rowcount)
c.commit()

cur.execute("select count(*) from oe_costs_item where is_active")
print("active remaining:", cur.fetchone()[0])
