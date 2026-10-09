# Mimic the golden runner's exact module resolution + call path for case 16.
import sys, io, os, json, tempfile, shutil
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb  # noqa
import pipeline  # noqa

tmp = Path(tempfile.mkdtemp())
demo = tmp / "demo"; demo.mkdir()
for f in (ROOT / "data" / "demo").glob("costdb_seed*.json"):
    shutil.copy2(f, demo)
proc = tmp / "processed"; proc.mkdir(parents=True)
db = tmp / "t.sqlite3"
os.environ["TENDEROPS_DB_PATH"] = str(db)
costdb.sync_json_sources(demo, db, force=True)

rec = json.load(open(ROOT / "tests/golden/cases/16_matcher_tricks/input.json", encoding="utf-8-sig"))
match_db = Path(proc).parent / "t.sqlite3"
print("match db:", match_db, "exists:", match_db.exists())
outs = []
for q in rec["queries"]:
    best, score, ev = pipeline.match_cost_v2({"desc": q["desc"], "unit": q["unit"], "qty": 1}, match_db)
    outs.append({"q": q["desc"][:40], "ref": (best or {}).get("ref"), "score": round(score, 2), "method": ev.get("method")})
for o in outs: print(o)
shutil.rmtree(tmp, ignore_errors=True)
