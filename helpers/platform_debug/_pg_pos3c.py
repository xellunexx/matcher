import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("""select code, left(description,90), unit, rate, source, is_active
               from oe_costs_item
               where description ilike '%блокове%багер%' or description ilike '%разбиване%бетон%'""")
print("razbivane beton rows:", cur.fetchall())
# any demolition per m2 at all
cur.execute("""select code, left(description,80), unit, rate
               from oe_costs_item where is_active
               and (description ilike '%разрушаване%' or description ilike '%събаряне%'
                    or description ilike '%разбиване%' or description ilike '%демонтаж%')
               and unit ~ 'м2|m2|м²'
               limit 20""")
print("\ndemolition per m2:", cur.fetchall())
