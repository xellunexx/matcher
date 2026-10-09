import sys, io, os, tempfile, shutil
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb, pipeline

tmp = Path(tempfile.mkdtemp())
demo = tmp / "demo"; demo.mkdir()
for f in (ROOT / "data" / "demo").glob("costdb_seed*.json"):
    shutil.copy2(f, demo)
db = tmp / "t.sqlite3"
costdb.sync_json_sources(demo, db, force=True)

NOISE = {"desc": "Шум около пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки", "unit": "м2", "qty": 1}

# pass 1: noise FIRST, alone
b, s, ev = pipeline.match_cost_v2(NOISE, db)
print("noise alone:", (b or {}).get("ref"), s, ev.get("method"))
for c in ev.get("top_candidates", [])[:5]:
    print("   cand:", c.get("ref"), c.get("score"), c.get("status"), str(c.get("desc"))[:55])

# pass 2: after a successful SEK query
pipeline.match_cost_v2({"desc": "ОСТЪКЛЯВАНЕ С 3 ММ СТЪКЛА В/У ДЪРВЕНИ РАМКИ", "unit": "м2", "qty": 1}, db)
b, s, ev = pipeline.match_cost_v2(NOISE, db)
print("noise after sek:", (b or {}).get("ref"), s, ev.get("method"))
for c in ev.get("top_candidates", [])[:5]:
    print("   cand:", c.get("ref"), c.get("score"), c.get("status"), str(c.get("desc"))[:55])
shutil.rmtree(tmp, ignore_errors=True)
