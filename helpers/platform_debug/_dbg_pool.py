import sys, io, tempfile, shutil, os
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

NOISE = "Шум около пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки обувки пясък пейки"
print("PYTHONHASHSEED:", os.environ.get("PYTHONHASHSEED"))
print("fts q:", costdb._fts_query(NOISE))
raw = costdb.search_candidates(db, NOISE, "м2", limit=40, include_pending=True)
print("pool size:", len(raw))
scored = []
for r in raw:
    v = pipeline._cost_row_view(r)
    s, mth, det = pipeline._candidate_score({"desc": NOISE, "unit": "м2"}, v)
    scored.append((s, v["status"][:4], mth, v["ref"][:40], str(v["desc"])[:60], det.get("reasons")))
scored.sort(key=lambda x: -x[0])
for x in scored[:12]:
    print(f"  {x[0]:6.1f} {x[1]:4} {x[2]:18} {x[3]:42} {x[4]}")
    print(f"        reasons={x[5]}")
shutil.rmtree(tmp, ignore_errors=True)
