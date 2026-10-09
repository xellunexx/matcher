# -*- coding: utf-8 -*-
import sys
import xlrd

sys.stdout.reconfigure(encoding="utf-8")

PATH = r"C:\Users\ochak\Downloads\КС -  Хисаря-всички части - за Възложителя.xls"
wb = xlrd.open_workbook(PATH)
KEYS = ("басейн", "вана", "pool", "джакуз", "компенсацион", "скимер", "филтр", "хале")

for sh in wb.sheets():
    hits = []
    for r in range(sh.nrows):
        row_text = " | ".join(str(sh.cell_value(r, c)) for c in range(sh.ncols))
        if any(k in row_text.lower() for k in KEYS):
            hits.append(r)
    if hits:
        print(f"\n===== {sh.name} ({len(hits)} hits) =====")
        for r in hits[:80]:
            vals = [str(sh.cell_value(r, c))[:55] for c in range(sh.ncols)]
            print(r, "|", " | ".join(vals).rstrip(" |"))
