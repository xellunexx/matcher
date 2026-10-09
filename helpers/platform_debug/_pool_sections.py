# -*- coding: utf-8 -*-
import sys
import xlrd

sys.stdout.reconfigure(encoding="utf-8")

PATH = r"C:\Users\ochak\Downloads\КС -  Хисаря-всички части - за Възложителя.xls"
wb = xlrd.open_workbook(PATH)

def dump(name, r0, r1):
    sh = wb.sheet_by_name(name)
    print(f"\n===== {name} rows {r0}-{r1} =====")
    for r in range(r0, min(r1, sh.nrows)):
        vals = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
        vals = [v for v in vals]
        if any(v for v in vals):
            print(r, "|", " | ".join(vals).rstrip(" |"))

# indoor pool detailed section
dump("Подробна КС -част АС", 1040, 1240)
