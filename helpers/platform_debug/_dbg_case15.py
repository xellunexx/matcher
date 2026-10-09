import sys, io, tempfile, shutil, json
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
proc = tmp / "processed"
rec = json.load(open(ROOT / "tests/golden/cases/15_adversarial_kss/input.json", encoding="utf-8-sig"))
for d in rec.get("documents", []):
    fx = d.get("fixtureFile")
    if fx:
        dst = proc / "files" / str(rec["id"]) / (f"{d['docId']}_{d['name']}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "tests/golden/cases/15_adversarial_kss/files" / fx, dst)
res = pipeline.process_tender(rec, demo, proc, llm_settings=None)
pack = json.loads((proc / f"{rec['id']}.json").read_text(encoding="utf-8-sig"))
for i, b in enumerate(pack["boq"]):
    m = b.get("match") or {}
    ref = (m.get("chosen") or m.get("proposal") or {}).get("ref") or ((m.get("top_candidates") or [{}])[0].get("ref"))
    print(f"boq.{i}: rule={b.get('rule')} flag={b.get('flag')} qty={b.get('qty')} unit={b.get('unit')} unitEur={b.get('unitEur')} sum={b.get('sumEur')}")
    print(f"   desc: {b.get('desc')[:85]}")
    print(f"   match: method={m.get('method')} score={m.get('score')} ref={ref}")
print("total:", pack["pricing"].get("totalExclVat"))
shutil.rmtree(tmp, ignore_errors=True)
