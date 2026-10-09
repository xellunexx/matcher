# -*- coding: utf-8 -*-
import sys, io, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

for p in [
    r'C:\lab\tenderops\zzzzzen_backup_20261005_1825\data\demo\costdb_seed_webanchors.json',
    r'C:\lab\tenderops\zzzzzen_backup_20261005_1825\data\demo\costdb_seed_sek_2026.json',
]:
    data = json.load(open(p, encoding='utf-8'))
    items = data if isinstance(data, list) else data.get('items', data)
    print(f'\n===== {p.split(chr(92))[-1]}: {len(items)} items')
    stats = collections.Counter(it.get('status') for it in items)
    kinds = collections.Counter((it.get('origin') or {}).get('kind') for it in items)
    print('statuses:', dict(stats))
    print('origin kinds:', dict(kinds))
    for it in items[:3]:
        print(json.dumps(it, ensure_ascii=False)[:400])
