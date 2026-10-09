import importlib.util, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = r"C:\lab\tenderops\tenderops"
def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + rf"\app\{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m
cdb = mod("costdb"); pipe = mod("pipeline")
db = ROOT + r"\tenderops.sqlite3"
con = cdb.connect(db)
for r in con.execute("select source_key,status,count(*) from cost_items group by source_key,status"):
    print(dict(r))
print("--- SEK12.801 row ---")
for r in con.execute("select code,desc,status,source_key,origin_ref,priority_for from (select *,0 priority_for from cost_items) where code like '%12.801%' or origin_ref like '%12.801%' limit 5"):
    print({k: r[k] for k in r.keys() if k != 'priority_for'})
con.close()
q = {"desc": "Остъкляване с 3 мм стъкла върху дървени рамки при ремонти", "unit": "м2", "qty": 1}
raw = cdb.search_candidates(db, q["desc"], q["unit"], limit=40, include_pending=True)
scored = []
for rr in raw:
    v = pipe._cost_row_view(rr)
    s, mth, det = pipe._candidate_score(q, v)
    scored.append((s, mth, v["status"], v["desc"][:45], v["code"], v["ref"][:40]))
scored.sort(key=lambda x: -x[0])
for s in scored[:10]:
    print(f"{s[0]:6.1f} {s[1]:16} {s[2][:4]} {s[3]:47} {s[4]}")
