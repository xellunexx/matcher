import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import openpyxl
from decimal import Decimal

wb = openpyxl.load_workbook(r"C:\Users\ochak\Downloads\test.xlsx" if False else r"C:\Users\ochak\Downloads\gb.xlsx")
ws = wb["BOQ"]

ones = 0
rates = []
sample_meta = None
for row in ws.iter_rows(min_row=3, values_only=True):
    desc, unit, qty, rate = row[1], row[2], row[3], row[4]
    if desc is None or unit is None or qty is None:
        continue
    try:
        r = Decimal(str(rate or 0))
    except Exception:
        continue
    rates.append(r)
    if r == 1:
        ones += 1
    if sample_meta is None and row[13]:
        sample_meta = row[13]

n = len(rates)
print(f"rows={n} rate==1: {ones} ({100*ones/n:.0f}%)")
import statistics
print("median rate:", statistics.median(rates))
# import source stamp
m = json.loads(sample_meta) if sample_meta else {}
print("import_source:", m.get("import_source"))
