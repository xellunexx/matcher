import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

boq = "5726cd49-1430-41ff-8ff7-a69886e0f3c0"
cur.execute("""select ordinal, left(description,60), unit, quantity, unit_rate, price_basis
               from oe_boq_position where boq_id=%s and description like '%%багер%%' order by ordinal limit 10""", (boq,))
for r in cur.fetchall():
    print(r)

cur.execute("""select price_basis, count(*),
               count(*) filter (where unit_rate::numeric > 0) rated
               from oe_boq_position where boq_id=%s group by 1 order by 2 desc""", (boq,))
print("\nby basis:", *cur.fetchall(), sep="\n  ")

cur.execute("""select count(*) from oe_boq_position where boq_id=%s and unit_rate::numeric=1.0""", (boq,))
print("\nrate=1.0 rows:", cur.fetchone()[0])
