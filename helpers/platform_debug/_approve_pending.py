import psycopg2

conn = psycopg2.connect(host="127.0.0.1", port=50554, user="postgres", dbname="postgres")
conn.autocommit = False
try:
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE oe_costs_item
           SET is_active = true,
               updated_at = now()
         WHERE metadata ? 'tenderops_id'
           AND is_active = false
        """
    )
    print("activated:", cur.rowcount)
    conn.commit()
    cur.execute(
        "SELECT is_active, count(*) FROM oe_costs_item "
        "WHERE metadata ? 'tenderops_id' GROUP BY 1 ORDER BY 1"
    )
    print("state now:", cur.fetchall())
    cur.execute("SELECT count(*) FROM oe_costs_item WHERE is_active")
    print("total active items (all rows):", cur.fetchone()[0])
finally:
    conn.close()
