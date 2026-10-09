import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
# is the buildly smr seed even in the ERP corpus?
cur.execute("""select source, count(*) from oe_costs_item
               where code like 'BUILDLY%' or id::text like '%buildly%'
               group by source""")
print("buildly rows:", cur.fetchall())
cur.execute("""select code, left(description,80), unit, rate, source, is_active
               from oe_costs_item
               where description ilike '%разбиване%' or description ilike '%БЛОКОВЕ%'""")
print("razbivane/block rows:", cur.fetchall())
# descriptions jsonb - multilingual?
cur.execute("""select code, descriptions from oe_costs_item
               where descriptions is not null and descriptions != '{}'::jsonb limit 3""")
for r in cur.fetchall(): print("desc-jsonb:", r)
