import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import psycopg2
c = psycopg2.connect(host="127.0.0.1", port=56729, user="postgres", dbname="postgres")
cur = c.cursor()
cur.execute("select count(*) from oe_costs_item where currency='BGN'")
print("before:", cur.fetchall())
cur.execute("""
    update oe_costs_item
    set rate = (rate::numeric / 1.95583)::numeric(24,6)::text,
        currency = 'EUR',
        metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object(
            'original_rate_bgn', rate,
            'original_currency', 'BGN',
            'conversion', 'EUR = BGN / 1.95583',
            'converted_at', now()::text
        ),
        updated_at = now()
    where currency = 'BGN'
      and rate ~ '^[0-9]+(\\.[0-9]+)?$'
""")
print("updated:", cur.rowcount)
cur.execute("select count(*) from oe_costs_item where currency='BGN'")
print("after:", cur.fetchall())
# sanity spot check
cur.execute("""select left(description,40), unit, rate, currency,
                      metadata->>'original_rate_bgn' orig
               from oe_costs_item where metadata->>'original_currency'='BGN'
               limit 5""")
for r in cur.fetchall(): print(r)
c.commit()
print("committed")
