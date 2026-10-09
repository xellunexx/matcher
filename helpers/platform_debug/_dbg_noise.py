import importlib.util, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = r"C:\lab\tenderops\tenderops"
def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + rf"\app\{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m
cdb = mod("costdb"); pipe = mod("pipeline"); mt = mod("matcher")
con = cdb.connect(ROOT + r"\tenderops.sqlite3")
q = "Шум около пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки"
r = con.execute("select * from cost_items where code='СЕК12.801'").fetchone()
v = pipe._cost_row_view(dict(r))
print("score_text:", v["score_text"][:160])
s, mth, det = pipe._candidate_score({"desc": q, "unit": "м2"}, v)
print("score:", s, mth, det.get("reasons"))
qn = cdb._domain_norm(q)
qt = set(mt.canonical_tokens(qn)); ct = set(mt.canonical_tokens(v["score_text"]))
print("qt:", sorted(qt))
print("shared:", qt & ct)
print("q_actions:", qt & mt._ACTION_CONCEPTS, "| c_actions:", ct & mt._ACTION_CONCEPTS)
print("q specs:", mt.extract_specs(qn), "| c specs:", mt.extract_specs(v["score_text"]))
# full pool ranking
raw = cdb.search_candidates(con if isinstance(con, str) else ROOT + r"\tenderops.sqlite3", q, "м2", limit=80, include_pending=True)
scored = []
for rr in raw:
    vv = pipe._cost_row_view(rr)
    ss, mm, dd = pipe._candidate_score({"desc": q, "unit": "м2"}, vv)
    scored.append((ss, vv["status"][:4], vv["desc"][:60], dd.get("reasons")))
scored.sort(key=lambda x: -x[0])
for x in scored[:8]: print(" pool:", x)
con.close()
