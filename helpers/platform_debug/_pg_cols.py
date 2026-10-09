import psycopg2

c = psycopg2.connect(host="127.0.0.1", port=50554, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute(
    "SELECT column_name, data_type FROM information_schema.columns "
    "WHERE table_name='oe_costs_item' ORDER BY ordinal_position"
)
for r in cur.fetchall():
    print(r)
