import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()
cur.execute("select table_name from information_schema.tables where table_schema='public' and (table_name like '%cost%' or table_name like '%price%' or table_name like '%match%' or table_name like '%catalog%' or table_name like '%item%') order by 1")
names = [r[0] for r in cur.fetchall()]
print("\n".join(names))
print("---", len(names), "tables")
for t in names:
    cur.execute(f'select count(*) from "{t}"')
    print(t, cur.fetchone()[0])
