# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

p = r'C:\lab\tenderops\zzzzzen_backup_20261005_1825\data\demo\costdb_seed_2026.json'
data = json.load(open(p, encoding='utf-8'))
items = data if isinstance(data, list) else data.get('items', data)
for i, it in enumerate(items):
    amt = (it.get('money') or {}).get('amount')
    if amt in (646.78, 1054.8):
        print(f"{amt:>8} | idx={i} | {it.get('desc','')[:70]} | {it.get('source','')[:75]}")
