# -*- coding: utf-8 -*-
"""Dump structure + pool-related rows from the Hisarya BoQ .xls."""
import sys
import xlrd

sys.stdout.reconfigure(encoding="utf-8")

PATH = r"C:\Users\ochak\Downloads\КС -  Хисаря-всички части - за Възложителя.xls"
wb = xlrd.open_workbook(PATH)
print("sheets:", wb.sheet_names())
for sh in wb.sheets():
    print(f"\n=== {sh.name}: {sh.nrows}x{sh.ncols} ===")
    # print first 12 rows to learn the layout
    for r in range(min(12, sh.nrows)):
        vals = [str(sh.cell_value(r, c))[:40] for c in range(sh.ncols)]
        print(r, "|", " | ".join(vals))
