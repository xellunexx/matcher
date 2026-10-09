import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
con.row_factory = sqlite3.Row
for r in con.execute(
    "SELECT id, code, desc, unit, amount_eur, status, origin_ref FROM cost_items "
    "WHERE desc LIKE '%тротоарна настилка%' OR desc LIKE '%тротоарни плочи%' OR desc LIKE '%Разрушаване на съществуваща%'"
):
    print(dict(r))
