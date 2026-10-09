import importlib.util, sys, io, tempfile, shutil
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "app" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m
cdb = mod("costdb"); pipe = mod("pipeline")
tmp = Path(tempfile.mkdtemp())
demo = tmp / "demo"; demo.mkdir()
for f in (ROOT / "data" / "demo").glob("costdb_seed*.json"):
    shutil.copy2(f, demo)
print("seed files:", sorted(p.name for p in demo.iterdir()))
db = tmp / "t.sqlite3"
res = cdb.sync_json_sources(demo, db, force=True)
print("synced")
best, score, ev = pipe.match_cost_v2({"desc": "Шум около пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки", "unit": "м2", "qty": 1}, db)
print("best:", (best or {}).get("ref"), "| score:", score, "| method:", ev.get("method"))
for c in (ev.get("candidates") or [])[:6]:
    print("  cand:", c.get("ref"), c.get("score"), c.get("status"), str(c.get("desc"))[:60])
# inspect SEK12.801 rows in this fresh db
con = cdb.connect(db)
for r in con.execute("select id, source_key, code, status, substr(desc,1,80), substr(extra_json,1,300) from cost_items where code='СЕК12.801'"):
    print("ROW:", r)
con.close()
shutil.rmtree(tmp, ignore_errors=True)
