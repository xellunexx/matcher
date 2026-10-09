import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()
for t in ("oe_costs_item", "oe_costs_catalog", "oe_cost_match_run", "oe_cost_match_result", "oe_cost_item_resource"):
    try:
        cur.execute(f'select count(*) from "{t}"')
        print(t, "=", cur.fetchone()[0])
    except Exception as e:
        c.rollback()
        print(t, "ERR", str(e).strip().splitlines()[0])

# columns of oe_costs_item
cur.execute("select column_name from information_schema.columns where table_name='oe_costs_item' order by ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("\noe_costs_item cols:", cols)

# row distribution if it has is_active / source
for col in ("is_active", "source", "catalog_id", "region"):
    if col in cols:
        cur.execute(f"select {col}, count(*) from oe_costs_item group by 1 order by 2 desc limit 15")
        print(f"\n{col}:", cur.fetchall())
