import sqlite3, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
con.row_factory = sqlite3.Row
print("total:", con.execute("SELECT COUNT(*) c FROM cost_items").fetchone()["c"])
for r in con.execute("SELECT status, COUNT(*) c FROM cost_items GROUP BY status"):
    print("status:", dict(r))
for r in con.execute("SELECT origin_ref, COUNT(*) c FROM cost_items GROUP BY origin_ref ORDER BY c DESC LIMIT 12"):
    print("src:", dict(r))
for r in con.execute("SELECT code, desc, unit, amount_eur, status, origin_ref FROM cost_items WHERE code LIKE '%СЕК%' OR code LIKE 'SEK%' ORDER BY code LIMIT 8"):
    print("sek:", dict(r))
