import sqlite3, json
c = sqlite3.connect(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\tenderops\tenderops.sqlite3")
r = c.execute("SELECT id, code, category, section, extra_json FROM cost_items WHERE source_key='seed_2026' LIMIT 2").fetchall()
for row in r:
    print(row[0], '|', row[1], '|', (row[2] or '')[:40], '|', (row[3] or '')[:40])
    print(json.dumps(json.loads(row[4]), ensure_ascii=False)[:900])
    print('---')
r = c.execute("SELECT id, code, extra_json FROM cost_items WHERE source_key='buildly_materials' LIMIT 1").fetchone()
print(r[0], '|', r[1])
print(json.dumps(json.loads(r[2]), ensure_ascii=False)[:900])
print('unit maxlen:', c.execute('SELECT MAX(LENGTH(unit)) FROM cost_items').fetchone()[0])
print('dup (code,region):', c.execute('SELECT COUNT(*) FROM (SELECT code, region FROM cost_items WHERE code != "" GROUP BY code, region HAVING COUNT(*)>1)').fetchone()[0])
print('dup ids:', c.execute('SELECT COUNT(*) FROM (SELECT id FROM cost_items GROUP BY id HAVING COUNT(*)>1)').fetchone()[0])
# what do code values look like?
print('code samples:', [x[0] for x in c.execute('SELECT code FROM cost_items WHERE code != "" LIMIT 10')])
# pending vs active by source
print('status x source:', [tuple(x) for x in c.execute('SELECT source_key, status, COUNT(*) FROM cost_items GROUP BY source_key, status ORDER BY 1,2')])
