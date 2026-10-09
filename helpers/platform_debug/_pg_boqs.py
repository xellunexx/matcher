import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

cur.execute("select column_name from information_schema.columns where table_name='oe_boq_boq' order by ordinal_position")
print("boq cols:", [r[0] for r in cur.fetchall()])

cur.execute("""select b.id, b.name, b.created_at, p.name, p.currency
               from oe_boq_boq b left join oe_projects_project p on p.id=b.project_id
               order by b.created_at desc limit 12""")
for r in cur.fetchall():
    print(r)

cur.execute("""select boq_id, count(*) n,
               count(*) filter (where price_basis is not null) priced,
               count(*) filter (where price_basis='corpus_currency_mismatch') fx
               from oe_boq_position group by boq_id order by n desc limit 10""")
print("\nper-boq:", *cur.fetchall(), sep="\n  ")
