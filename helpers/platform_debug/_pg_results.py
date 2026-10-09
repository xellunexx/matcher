import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

runs = ("cf6dc445-1dfe-40ca-a6d3-47049e70b000", "7c6da7da-6658-4ca1-b26a-2792bf7018f3", "6f8dcd64-f2e8-4414-b872-53c99a18d9b4")
cur.execute("select count(*) from oe_cost_match_result where run_id in %s", (runs,))
print("total results:", cur.fetchone()[0])
for col in ("tier", "confidence", "decision_state"):
    cur.execute(f"select {col}, count(*) from oe_cost_match_result where run_id in %s group by 1 order by 2 desc", (runs,))
    print(col, cur.fetchall())
cur.execute("select count(*) from oe_cost_match_result where run_id in %s and suggested_rate is not null", (runs,))
print("with suggested_rate:", cur.fetchone()[0])
cur.execute("""select line_no, source_description, source_unit, tier, confidence, suggested_description, suggested_unit, suggested_rate, suggested_currency
               from oe_cost_match_result where run_id in %s and suggested_rate is not null order by line_no limit 12""", (runs,))
for r in cur.fetchall():
    print(r)
