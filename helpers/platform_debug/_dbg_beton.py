import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
con.row_factory = sqlite3.Row
for r in con.execute(
    "SELECT id, desc, unit, amount_eur, status, origin_ref FROM cost_items "
    "WHERE origin_ref LIKE '%храброво%' AND (desc LIKE '%бетон%' OR desc LIKE '%БЕТОН%' OR desc LIKE '%Бетон%')"
):
    print(dict(r))
print('--- chiller _inve_prices rows ---')
for r in con.execute(
    "SELECT id, desc, unit, amount_eur, status, origin_ref FROM cost_items WHERE origin_ref LIKE '%inve_prices%'"
):
    print(dict(r))
