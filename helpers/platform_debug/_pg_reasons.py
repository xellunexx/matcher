import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

# the demolition result rows
cur.execute("""select line_no, tier, confidence, suggested_unit, suggested_rate, suggested_currency,
                      reason_codes, factors::text
               from oe_cost_match_result
               where source_description like 'Разрушаване на сгради%'
               order by created_at desc limit 6""")
for r in cur.fetchall():
    print(r, "\n")

# how many results carry unit_mismatch
cur.execute("""select count(*) from oe_cost_match_result
               where run_id in (select id from oe_cost_match_run where notes::jsonb->>'boq_id'='5726cd49-1430-41ff-8ff7-a69886e0f3c0')
                 and reason_codes::text like '%unit_mismatch%'""")
print("unit_mismatch results:", cur.fetchone()[0])
