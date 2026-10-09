import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
BOQ = "3824c126-e054-4b65-aa51-16ddc493d821"
cur.execute("""select ordinal, left(description,45), unit, unit_rate, price_basis,
                      metadata->'cost_match'->>'unit' sug_unit,
                      metadata->'cost_match'->>'applied_rate' applied
               from oe_boq_position where boq_id=%s
               and price_basis='corpus_unit_mismatch' and unit_rate::numeric>0""", (BOQ,))
for r in cur.fetchall(): print(r)
