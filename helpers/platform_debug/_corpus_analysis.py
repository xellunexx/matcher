import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()

print("=== m2-priced demolition/sъбаряне rows in corpus ===")
cur.execute("""select code, left(description,90), unit, rate, source
               from oe_costs_item
               where is_active and (description ilike '%събаряне%' or description ilike '%разрушаване%'
                      or description ilike '%разбиване%' or description ilike '%демонтаж%')
               and (unit ilike 'м2%' or unit ilike '%m2%')
               order by rate::numeric""")
rows = cur.fetchall()
print(len(rows), "rows")
for r in rows[:15]: print(" ", r)

print("\n=== cable bedding rows (кабел + подложка/основа/легло) ===")
cur.execute("""select code, left(description,95), unit, rate, source
               from oe_costs_item
               where is_active and (description ilike '%кабел%' )
               and (description ilike '%подложка%' or description ilike '%основа%' or description ilike '%легло%'
                    or description ilike '%пясъчна%' or description ilike '%беластир%')
               order by source, rate::numeric desc""")
for r in cur.fetchall()[:20]: print(" ", r)

print("\n=== generic 'кабел' rows per m ===")
cur.execute("""select count(*), min(rate::numeric), max(rate::numeric), avg(rate::numeric)::numeric(10,2)
               from oe_costs_item where is_active and description ilike '%кабел%' and unit ilike 'м%'""")
print(cur.fetchone())

print("\n=== Buildly SMR coverage of zero-price positions ===")
# What would the 233 zero rows even match? Sample their descriptions:
cur.execute("""select ordinal, left(description,70), unit from oe_boq_position
               where boq_id='3824c126-e054-4b65-aa51-16ddc493d821'
               and unit_rate::numeric=0 and node_type='position' order by ordinal limit 30""")
for r in cur.fetchall(): print(" ", r)
