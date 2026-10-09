import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import openpyxl
from decimal import Decimal

wb = openpyxl.load_workbook(r"C:\Users\ochak\Downloads\test.xlsx")
ws = wb["BOQ"]

priced = zero_rate = no_qty = sections = 0
total = Decimal(0)
basis = {}
suspicious = []      # rate == 1 or absurd
top = []
ones = []
for row in ws.iter_rows(min_row=3, values_only=True):
    pos, desc, unit, qty, rate, tot, cur = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
    meta = row[13]
    if desc is None:
        continue
    if unit is None or qty is None:
        sections += 1
        continue
    try:
        r = Decimal(str(rate or 0))
        t = Decimal(str(tot or 0))
    except Exception:
        continue
    b = None
    if meta:
        try:
            m = json.loads(meta)
            cm = m.get("cost_match") or {}
            b = cm.get("tier") or m.get("price_basis")
        except Exception:
            pass
    if r > 0:
        priced += 1
        total += t
        top.append((t, pos, desc, unit, r))
        if r == 1:
            ones.append((pos, desc, unit))
    else:
        zero_rate += 1
    if b:
        basis[b] = basis.get(b, 0) + 1

print(f"leaf rows priced={priced} zero={zero_rate} sections={sections}")
print("grand total:", total)
print("basis:", basis)
print("\ntop 10 totals:")
for t, pos, d, u, r in sorted(top, reverse=True)[:10]:
    print(f"  {pos} {d[:45]} | {u} rate={r} tot={t}")
print(f"\nrate==1.0 rows: {len(ones)}")
for pos, d, u in ones[:15]:
    print(f"  {pos} {d[:55]} ({u})")
