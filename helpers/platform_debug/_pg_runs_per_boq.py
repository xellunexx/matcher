import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

cur.execute("""select id, name, item_count, status, created_at, notes::text
               from oe_cost_match_run order by created_at desc limit 15""")
for r in cur.fetchall():
    print(r[:5], "| boq:", r[5][:60] if r[5] else None)

print("\n-- runs per boq_id:")
cur.execute("""select notes::jsonb->>'boq_id' boq, count(*), sum(item_count), max(created_at)
               from oe_cost_match_run group by 1 order by 4 desc""")
for r in cur.fetchall():
    print(r)
