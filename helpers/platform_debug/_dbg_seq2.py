import sys, io, tempfile, shutil, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb, pipeline

rec = json.load(open(ROOT / "tests/golden/cases/16_matcher_tricks/input.json", encoding="utf-8-sig"))
queries = [q["desc"] for q in rec["queries"]]
NOISE = queries[8]

tmp = Path(tempfile.mkdtemp())
demo = tmp / "demo"; demo.mkdir()
for f in (ROOT / "data" / "demo").glob("costdb_seed*.json"):
    shutil.copy2(f, demo)
db = tmp / "t.sqlite3"
costdb.sync_json_sources(demo, db, force=True)

def run(desc, unit="м2"):
    b, s, ev = pipeline.match_cost_v2({"desc": desc, "unit": unit, "qty": 1}, db)
    return (b or {}).get("ref"), s, ev.get("method")

# baseline
print("baseline:", run(NOISE))

# each single predecessor, then noise
for i in range(8):
    r1 = run(queries[i])
    r2 = run(NOISE)
    print(f"after q{i} ({queries[i][:40]!r}) -> {r1} | noise -> {r2}")

# prefixes
for upto in (2, 4, 5, 6, 7):
    for i in range(upto):
        run(queries[i])
    print(f"prefix q0..q{upto-1}: noise -> {run(NOISE)}")
shutil.rmtree(tmp, ignore_errors=True)
