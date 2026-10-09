import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

cur.execute("select column_name from information_schema.columns where table_name='oe_boq_position' order by ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("position cols:", cols)

boq = "5726cd49-1430-41ff-8ff7-a69886e0f3c0"
cur.execute("select count(*) from oe_boq_position where boq_id=%s", (boq,))
print("positions:", cur.fetchone()[0])
for col in ("unit_rate", "price_basis", "rate_source", "match_status"):
    if col in cols:
        cur.execute(f"select count(*) from oe_boq_position where boq_id=%s and {col} is not null", (boq,))
        print(f"  with {col}:", cur.fetchone()[0])
        cur.execute(f"select {col}, count(*) from oe_boq_position where boq_id=%s and {col} is not null group by 1 order by 2 desc limit 8", (boq,))
        print("   ", cur.fetchall()[:8])
