# -*- coding: utf-8 -*-
"""Gate 1 regression battery: schema validation, immutable env versions,
DB-native human resolutions, idempotent match persistence. Offline."""
import importlib.util, json, shutil, sqlite3, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")

def mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

V, C = mod("validate"), mod("costdb")
ok = []

# ---- 1: runtime schema validation -----------------------------------------
good = {"tenderId": 1, "generatedAt": "x", "pipe": {}, "documents": [],
        "clarifications": [], "rules": [], "llm": {},
        "boq": [{"key": "R1", "no": "1.0", "desc": "нещо реално", "section": "A", "sub": "",
                 "unit": "м2", "qty": 1.0, "rule": "EST", "unitEur": 0.0, "sumEur": 0.0,
                 "note": "", "flag": "COST_NOT_FOUND"}],
        "pricing": {"totalExclVat": 0.0, "vatRate": 0.2, "vat": 0.0, "totalInclVat": 0.0,
                    "costEnvVersion": 1}}
assert V.validate(good, V.PACK) == [], V.validate(good, V.PACK)
ok.append("validator: valid pack passes")

bad = json.loads(json.dumps(good))
bad["boq"][0]["rule"] = "MAYBE"  # not in enum
errs = V.validate(bad, V.PACK)
assert errs and any("enum" in e for e in errs)
ok.append(f"validator: bad rule rejected loudly ({errs[0]})")

bad2 = json.loads(json.dumps(good)); del bad2["boq"][0]["qty"]; bad2["pricing"]["totalExclVat"] = -5
errs2 = V.validate(bad2, V.PACK)
assert any("missing required 'qty'" in e for e in errs2) and any("below minimum" in e for e in errs2)
ok.append("validator: missing qty + negative total both reported")
try:
    V.assert_pack(bad)
    raise AssertionError("assert_pack must raise")
except V.ContractViolation as e:
    ok.append("validator: assert_pack raises ContractViolation")

# ---- 2: immutable cost-env versions ----------------------------------------
tmp = Path(tempfile.mkdtemp())
demo = tmp / "demo"; demo.mkdir()
shutil.copy2(ROOT / "data" / "demo" / "costdb_seed_sek_2026.json", demo)
db = tmp / "t.sqlite3"
C.sync_json_sources(demo, db, force=True)
v1 = C.ensure_env_version(db)
v1b = C.ensure_env_version(db)   # unchanged -> same id, no new row
assert v1 == v1b, (v1, v1b)
ok.append(f"env: unchanged sync -> same env #{v1}")

d = json.load(open(demo / "costdb_seed_sek_2026.json", encoding="utf-8-sig"))
r0 = json.loads(json.dumps(d[0])); r0["id"] = "SEK-TST"; r0["money"]["amount"] = 1.0
d.append(r0)
json.dump(d, open(demo / "costdb_seed_sek_2026.json", "w", encoding="utf-8"), ensure_ascii=False)
C.sync_json_sources(demo, db)
v2 = C.ensure_env_version(db, note="test mutation")
assert v2 != v1 and v2 > v1
con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
r1 = con.execute("SELECT * FROM cost_env_versions WHERE id=?", (v1,)).fetchone()
r2 = con.execute("SELECT id FROM runs ORDER BY id").fetchall()
assert json.loads(r1["sources_json"])  # v1 row intact, still queryable
ok.append(f"env: seed change -> new immutable version #{v2}; old version row preserved")
run_new = C.run_open(db, 999)
C.pin_run_env(db, run_new, v2)
assert con.execute("SELECT cost_env_id FROM runs WHERE id=?", (run_new,)).fetchone()[0] == v2
ok.append("env: run pinned to post-sync version (pin_run_env)")
con.close()

# ---- 3: human resolutions in SQLite ----------------------------------------
legacy = tmp / "resolutions.json"
legacy.write_text(json.dumps({"R9": {"note": "цената е от доставчик Иванов", "by": "ivan", "at": "2026-08-20"}},
                             ensure_ascii=False), encoding="utf-8")
n = C.import_resolutions_json(db, legacy)
assert n == 1
C.upsert_resolution(db, 606060, "R12", "потвърдено 350 €/бр", actor="maria", rationale="оферта №12/26")
C.upsert_resolution(db, 606060, "R12", "потвърдено 355 €/бр", actor="maria", rationale="корекция")
con = sqlite3.connect(str(db))
cnt = con.execute("SELECT COUNT(*) FROM human_resolutions WHERE tender_id=606060 AND boq_key='R12'").fetchone()[0]
assert cnt == 1, cnt
con.close()
res = C.get_resolutions(db)
assert " 355 " in res["R12"]["note"] and res["R12"]["rationale"] == "корекция" and res["R9"]["by"] == "ivan"
ok.append("resolutions: legacy import + upsert-in-place + rationale + UI shape")

# ---- 4: idempotent match persistence ---------------------------------------
line = {"key": "R5", "desc": "бетон С25/30", "unit": "M3", "qty": 10}
ev = {"method": "exact_description", "confidence": "high", "top_candidates": [{"id": "A", "score": 90}]}
C.record_match(db, 606060, line, "SEK-СЕК25-001", "exact_description", 90.0, 12.0, "high", ev)
C.record_match(db, 606060, line, "SEK-СЕК25-001", "exact_description", 90.0, 12.0, "high", ev)
C.record_match(db, 606060, line, "OTHER-1", "semantic", 66.0, 5.0, "low", ev)
con = sqlite3.connect(str(db))
m_all = con.execute("SELECT COUNT(*) FROM matches WHERE tender_id=606060 AND boq_key='R5'").fetchone()[0]
c_all = con.execute("SELECT COUNT(*) FROM match_candidates mc JOIN matches m ON m.id=mc.match_id WHERE m.tender_id=606060").fetchone()[0]
con.close()
assert m_all == 2, m_all           # twice-same decision = 1 row; different chosen = new row
assert c_all == 2, c_all           # 1 candidate each, replaced
ok.append("matches: rerun same decision -> in-place refresh (no duplicate); changed decision -> new row")

print("\n".join("PASS " + s for s in ok))
print("GATE-1 REGRESSION: ALL GREEN")
shutil.rmtree(tmp, ignore_errors=True)
