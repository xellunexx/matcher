import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select alternatives::text from oe_cost_match_result
               where source_description like 'Разрушаване на сгради и басейни%'
               order by created_at desc limit 2""")
for (alt,) in cur.fetchall():
    try:
        for a in json.loads(alt)[:8]:
            print(json.dumps(a, ensure_ascii=False)[:200])
    except Exception as e:
        print(alt[:600])
    print("---")
