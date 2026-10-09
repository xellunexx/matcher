# -*- coding: utf-8 -*-
import openpyxl

wb = openpyxl.load_workbook(r"C:\Users\ochak\Downloads\kcc2 (2).xlsx", data_only=True)
ws = wb.active

bad = ["захранващ блок", "обучение", "отвор в стената", "пач панел",
       "разделителен панел", "аранжиращ", "вибрационен", "розетк"]

priced = unpriced = 0
total = 0.0
flagged = []
for row in ws.iter_rows(min_row=3):
    pos, desc, unit, qty, rate = (row[i].value for i in (0, 1, 2, 3, 4))
    if desc is None:
        continue
    d = str(desc)
    hit = any(m in d.lower() for m in bad)
    if rate in (None, "", 0):
        unpriced += 1
        if hit:
            flagged.append((pos, d[:70], "BLANK"))
    else:
        priced += 1
        try:
            total += float(rate) * (float(qty) if qty else 1)
        except Exception:
            pass
        if hit:
            flagged.append((pos, d[:70], rate))

print(f"priced={priced} unpriced={unpriced} total=€{total:,.2f}\n")
for pos, d, r in flagged:
    print(f"  pos {pos}: {r}  | {d}")
