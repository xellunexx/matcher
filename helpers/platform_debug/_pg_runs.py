import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

cur.execute("select column_name from information_schema.columns where table_name='oe_cost_match_run' order by ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("run cols:", cols)

cur.execute("select * from oe_cost_match_run order by created_at desc limit 6")
rows = cur.fetchall()
for r in rows:
    d = dict(zip(cols, r))
    print("\n--- run:", {k: (str(v)[:120]) for k, v in d.items()})

cur.execute("select column_name from information_schema.columns where table_name='oe_cost_match_result' order by ordinal_position")
rcols = [r[0] for r in cur.fetchall()]
print("\nresult cols:", rcols)
