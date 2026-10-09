# -*- coding: utf-8 -*-
import openpyxl

wb = openpyxl.load_workbook(r"C:\Users\ochak\Downloads\kcc2 (1).xlsx", data_only=True)
ws = wb.active

bad_markers = ["захранващ блок", "обучение", "отвор в стената", "пач панел",
               "разделителен панел", "аранжиращ", "вибрационен", "пералня",
               "надлеглови", "розетк"]

unpriced = 0
priced = 0
no_rate = []
total = 0.0
for row in ws.iter_rows(min_row=3):
    pos, desc, unit, qty, rate = (row[i].value for i in (0, 1, 2, 3, 4))
    if desc is None:
        continue
    d = str(desc)
    hit = any(m in d.lower() for m in bad_markers)
    if rate in (None, "", 0):
        unpriced += 1
        if hit:
            print(f"UNPRICED pos {pos}: {d[:80]}")
    else:
        priced += 1
        try:
            total += float(rate) * (float(qty) if qty else 1)
        except Exception:
            pass
        if hit:
            print(f"PRICED   pos {pos}: {d[:70]} -> {rate}")
print(f"\npriced={priced} unpriced={unpriced} total=€{total:,.2f}")
