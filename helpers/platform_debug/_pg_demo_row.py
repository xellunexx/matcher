import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

boq = "5726cd49-1430-41ff-8ff7-a69886e0f3c0"
cur.execute("""select ordinal, left(description,70), unit, quantity, unit_rate, price_basis, confidence
               from oe_boq_position where boq_id=%s and description like '%%багер%%' order by ordinal limit 10""", (boq,))
for r in cur.fetchall():
    print(r)

# currency of project + what currencies the corpus suggested
cur.execute("""select suggested_currency, count(*), count(*) filter (where suggested_rate is not null)
               from oe_cost_match_result
               where run_id in (select id from oe_cost_match_run where notes::text like %s)
               group by 1""", ('%5726cd49%',))
print("\nsuggested currencies:", cur.fetchall())

# what currency do cost items carry?
cur.execute("select currency, count(*) from oe_costs_item group by 1 order by 2 desc")
print("corpus currencies:", cur.fetchall())

# project currency
cur.execute("select table_name from information_schema.tables where table_name like '%project%' order by 1")
print("\nproject tables:", [r[0] for r in cur.fetchall()])
