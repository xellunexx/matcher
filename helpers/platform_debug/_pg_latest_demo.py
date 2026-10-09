import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

cur.execute("""select r.source_description, r.tier, r.confidence, r.suggested_description,
                      r.suggested_unit, r.suggested_rate, r.suggested_currency, r.reason_codes
               from oe_cost_match_result r
               join oe_cost_match_run run on run.id = r.run_id
               where r.source_description like 'Разрушаване на сгради и басейни%'
               order by r.created_at desc limit 3""")
for r in cur.fetchall():
    print([str(x)[:90] for x in r], "\n")
