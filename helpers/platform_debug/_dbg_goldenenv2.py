import importlib.util, sys, io, tempfile, shutil, json
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
db = tmp / "t.sqlite3"
cdb.sync_json_sources(demo, db, force=True)
con = cdb.connect(db)
con.row_factory = None
rows = con.execute("select source_key, code, status, substr(desc,1,70), substr(coalesce(extra_json,''),1,400) from cost_items where code='СЕК12.801'").fetchall()
for r in rows:
    print("ROW:", r)
queries = [
    "Остъкляване с 4 мм стъкла върху дървени рамки при ремонти",
    "Остъкляване с 5 мм стъкла върху дървени рамки при ремонти",
    "СЕК12.801 Остъкляване със стъкла върху дървени рамки при ремонти",
    "остъкляване със стъкла върху дървени рамки по позиция СЕК12.801",
    "ОСТЪКЛЯВАНЕ С 3 ММ СТЪКЛА В/У ДЪРВЕНИ РАМКИ",
    "на за от с по в до при вкл",
    "несъществуващ продукт люляк агитан",
    "",
    "Шум около пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки",
]
for i, q in enumerate(queries):
    best, score, ev = pipe.match_cost_v2({"desc": q, "unit": "м2", "qty": 1}, db)
    print(f"q{i}: best={(best or {}).get('ref')} score={score} method={ev.get('method')}")
con.close()
shutil.rmtree(tmp, ignore_errors=True)
