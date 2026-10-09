# -*- coding: utf-8 -*-
import sys, io, json, openpyxl
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
wb = openpyxl.load_workbook(r'C:\Users\ochak\Downloads\kcc2 (8).xlsx', read_only=True, data_only=True)
ws = wb[wb.sheetnames[0]]
rows = list(ws.iter_rows(values_only=True)); wb.close()
hdr = rows[1]; cols = {n: i for i, n in enumerate(hdr) if n}
for r in rows[2:]:
    try:
        v = float(r[cols['Unit Rate']]) if r[cols['Unit Rate']] is not None else 0
    except Exception:
        v = 0
    if v in (825.47, 4856.85) and r[cols['Description']]:
        try:
            cm = json.loads(r[cols['Metadata JSON']] or '{}').get('cost_match') or {}
        except Exception:
            cm = {}
        desc = str(r[cols['Description']])[:56]
        print(v, '|', desc, '|', str(cm.get('code'))[:26], '|', str(cm.get('desc'))[:60], '|', str(cm.get('method') or cm.get('score'))[:18])
