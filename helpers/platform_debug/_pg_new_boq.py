import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
BOQ = "3824c125-e054-4b65-aa51-16ddc493f821"
cur.execute("""select id, status, created_at, updated_at, item_count, left(notes::text,200)
               from oe_cost_match_run where notes::text like %s order by created_at""", ('%'+BOQ+'%',))
print("runs (via notes):")
for r in cur.fetchall(): print(" ", r)
# try direct boq_id column? it doesn't exist; run->boq via notes or project
cur.execute("""select b.id, b.name, b.project_id, p.name, p.currency
               from oe_boq_boq b join oe_projects_project p on p.id=b.project_id
               where b.id=%s""", (BOQ,))
print("boq+project:", cur.fetchall())
cur.execute("""select price_basis, count(*),
                      count(*) filter (where unit_rate::numeric>0) priced
               from oe_boq_position where boq_id=%s group by price_basis""", (BOQ,))
print("positions by basis:", cur.fetchall())
# all runs from last hour
cur.execute("""select id, status, created_at, item_count from oe_cost_match_run
               order by created_at desc limit 5""")
print("latest runs:", cur.fetchall())
