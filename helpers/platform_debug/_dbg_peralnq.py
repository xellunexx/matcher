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
r = con.execute("select * from cost_items where desc like 'Пералня%' limit 1").fetchone()
v = pipe._cost_row_view(dict(r))
q = "Термопомпен чилър въздух/вода 60 kW, работни граници до -20 в отопл."
print("score_text:", v["score_text"][:140])
s, mth, det = pipe._candidate_score({"desc": q, "unit": "бр."}, v)
print("score:", s, mth)
print("reasons:", det.get("reasons"))
print("exact_norm q:", cdb.exact_norm(q)[:90])
print("exact_norm c:", cdb.exact_norm(v["desc"])[:90])
print("code q:", repr(pipe._extract_code(q)), "| code c:", repr(v["code"]))
con.close()
