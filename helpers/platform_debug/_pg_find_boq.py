import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select id, name, project_id, created_at from oe_boq_boq
               order by created_at desc limit 8""")
print("latest boqs:")
for r in cur.fetchall(): print(" ", r)
cur.execute("""select id from oe_boq_boq where id::text ilike '3824c125%'""")
print("id match:", cur.fetchall())
