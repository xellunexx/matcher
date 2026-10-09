# -*- coding: utf-8 -*-
"""E2E proof of the merged SQLite engine: sync -> probes -> real tender run -> match audit."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
os.environ["TENDEROPS_DB_PATH"] = str(ROOT / "_test" / "tenderops.sqlite3")
shutil.rmtree(ROOT / "_test", ignore_errors=True)

def mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

cdb = mod("costdb")
pipe = mod("pipeline")
db = Path(os.environ["TENDEROPS_DB_PATH"])

print("== sync:", json.dumps(cdb.sync_json_sources(ROOT / "data" / "demo", db, force=True), ensure_ascii=False)[:500])
print("== stats:", json.dumps(cdb.stats(db), ensure_ascii=False))
print("== sources:", [s["source_key"] for s in cdb.source_registry(db)])

# probes over the canonical engine
probe = {"desc": "Остъкляване с 3 мм стъкла върху дървени рамки при ремонти", "unit": "м2", "qty": 1}
best, score, ev = pipe.match_cost_v2(probe, str(db))
print("== probe SEK12.801:", (best or {}).get("ref"), "|", score, "|", ev.get("method"), "| margin", ev.get("margin"))
probe2 = {"desc": "Разбиване на неармирана бетонна настилка ръчно с почистване", "unit": "м2", "qty": 1}
b2, s2, e2 = pipe.match_cost_v2(probe2, str(db))
print("== probe SEK49.027:", (b2 or {}).get("ref"), "|", round(s2, 1), e2.get("method"))

reg_path = ROOT / "dist" / "data" / "demo" / "registry.json"
if not reg_path.exists():
    reg_path = ROOT / "data" / "demo" / "registry.json"  # dev checkout has no packaged dist/
reg = json.load(open(reg_path, encoding="utf-8-sig"))
rec = next(t for t in reg["tenders"]
           if (ROOT / "data" / "demo" / "processed" / "files" / str(t["id"])).is_dir())
# seed the doc cache so download_docs stays local (no ЦАИС network dependency)
src_cache = ROOT / "data" / "demo" / "processed" / "files" / str(rec["id"])
dst_cache = ROOT / "_test" / "processed" / "files" / str(rec["id"])
dst_cache.mkdir(parents=True, exist_ok=True)
for f in src_cache.iterdir():
    if f.is_file():
        shutil.copy2(f, dst_cache / f.name)
res = pipe.process_tender(rec, ROOT / "data" / "demo", ROOT / "_test" / "processed", llm_settings=None)
pk = res["pack"]
ncsv = sum(1 for l in pk["boq"] if l["rule"] == "CSV")
print(f"== {rec['id']}: rows", len(pk["boq"]), "| priced", ncsv, "| total", pk["pricing"]["totalExclVat"])
from collections import Counter
print("== methods:", dict(Counter(l["match"]["method"] for l in pk["boq"] if l.get("match"))))
anch = sum(1 for l in pk["boq"] if l["rule"] == "EST" and "web-анкер" in (l.get("note") or ""))
print("== EST with web-anchor hint:", anch)
msc = cdb.connect(db)
print("== matches table:", msc.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
      "| candidates:", msc.execute("SELECT COUNT(*) FROM match_candidates").fetchone()[0])
