import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres", connect_timeout=5)
cur = c.cursor()
cur.execute("select table_schema, count(*) from information_schema.tables group by 1 order by 1")
print("schemas:", cur.fetchall())
cur.execute("select count(*) from pg_stat_activity where datname='postgres'")
print("conns to postgres:", cur.fetchone())
cur.execute("show data_directory")
print("pgdata:", cur.fetchone())
