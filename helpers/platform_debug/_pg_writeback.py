import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()

# find positions table for the boq
cur.execute("select table_name from information_schema.tables where table_schema='public' and (table_name like '%boq%' or table_name like '%position%' or table_name like '%estimate%') order by 1")
print([r[0] for r in cur.fetchall()])
