import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
for db in ("postgres", "openestimate", "openconstructionerp", "erp"):
    try:
        c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname=db, connect_timeout=5)
        cur = c.cursor()
        cur.execute("select count(*) from cost_items")
        total = cur.fetchone()[0]
        cur.execute("select count(*) from cost_items where is_active")
        active = cur.fetchone()[0]
        print(db, "| cost_items total:", total, "| active:", active)
        cur.execute("select source, count(*) from cost_items group by source order by 2 desc limit 12")
        for r in cur.fetchall():
            print("   ", r)
        c.close()
        break
    except Exception as e:
        print(db, "->", str(e)[:80])
