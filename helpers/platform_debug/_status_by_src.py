import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
con.row_factory = sqlite3.Row
for r in con.execute("""
    SELECT source_key, status, COUNT(*) c,
           SUM(CASE WHEN amount_eur IS NOT NULL AND amount_eur > 0 THEN 1 ELSE 0 END) priced
    FROM cost_items GROUP BY source_key, status ORDER BY source_key, status
"""):
    print(dict(r))
