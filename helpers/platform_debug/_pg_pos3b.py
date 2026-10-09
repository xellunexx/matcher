import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
# demolition/razbivane rows priced per m2 - is there a same-unit candidate?
cur.execute("""select code, left(description,90), unit, rate, source
               from oe_costs_item where is_active
               and (description ilike '%плоч%' or description ilike '%настилк%'
                    or description ilike '%тротоар%' or description ilike '%бетон%')
               and (unit ilike '%м2%' or unit ilike '%m2%' or unit ilike '%кв.м%')
               order by rate::numeric limit 30""")
print("area-priced concrete/slab rows:")
for r in cur.fetchall(): print(" ", r)
# the RIDX item itself
cur.execute("""select code, description, unit, rate, source from oe_costs_item
               where code='RIDX_KARI_KARILI_KAME'""")
print("\nmatched item:", cur.fetchall())
