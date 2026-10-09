import importlib.util, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = r"C:\lab\tenderops\tenderops"
def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + rf"\app\{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m
cdb = mod("costdb"); pipe = mod("pipeline"); mt = mod("matcher")
con = cdb.connect(ROOT + r"\tenderops.sqlite3")
r = con.execute("select * from cost_items where desc like 'Пералня%' limit 1").fetchone()
v = pipe._cost_row_view(dict(r))
print("FULL score_text:", v["score_text"])
q = cdb._domain_norm("Термопомпен чилър въздух/вода 60 kW, работни граници до -20 в отопл.")
qt = set(mt.canonical_tokens(q)); ct = set(mt.canonical_tokens(v["score_text"]))
print("shared:", qt & ct)
con.close()
