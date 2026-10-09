import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
BOQ = "3824c126-e054-4b65-aa51-16ddc493d821"
cur.execute("""select ordinal, description, unit, unit_rate, price_basis,
                      metadata->'cost_match' cm
               from oe_boq_position where boq_id=%s and ordinal='3'""", (BOQ,))
r = cur.fetchone()
print("pos3:", r[:5])
print(json.dumps(r[5], ensure_ascii=False, indent=1)[:1500])
# what corpus demolition items exist at all, and their units
cur.execute("""select code, left(description,80), unit, rate, currency, source
               from oe_costs_item where is_active
               and (description ilike '%разбиване%' or description ilike '%разрушаване%'
                    or description ilike '%къртене%' or description ilike '%събаряне%'
                    or description ilike '%демонтаж%бетон%' or description ilike '%бетон%демонтаж%')
               order by unit limit 40""")
print("\ncorpus demolition rows:")
for row in cur.fetchall(): print(" ", row)
