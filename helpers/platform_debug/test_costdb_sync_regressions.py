# -*- coding: utf-8 -*-
"""Regression lock for costdb sync semantics (2026-09-06 incidents):

1. prune must NEVER touch sources whose seed files still exist but were skipped
   (SHA unchanged) — this was the corpus-wipe bug caught live.
2. prune MUST remove a source whose seed file actually disappeared (intended behavior).
3. no-op sync must be fast (no unconditional FTS rebuild) and leave the index intact.
4. a changed seed must re-import AND land in the FTS index.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent


def mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def seed(path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def item(i, desc, unit="м2", price=10.0, status="active"):
    return {"id": f"X{i}", "name": desc, "desc": desc, "category": "test", "section": "T",
            "unit": unit,
            "money": {"amount": price, "currency": "EUR", "vatIncluded": False, "asOf": "2026-01-01"},
            "origin": {"kind": "imported_workbook", "ref": f"R{i}"},
            "status": status}


failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


cdb = mod("costdb")
tmp = Path(tempfile.mkdtemp(prefix="costdb_sync_"))
try:
    demo = tmp / "demo"
    demo.mkdir()
    db = tmp / "test.sqlite3"

    unique_a = "АлфаУникаленМатериалКс123"
    unique_b = "БетаДругМатериалКс456"
    seed(demo / "costdb_seed_a.json", [item(1, unique_a)])
    seed(demo / "costdb_seed_b.json", [item(2, unique_b)])

    s1 = cdb.sync_json_sources(demo, db)
    check("initial import adds both sources", s1["added"] == 2, f"added={s1['added']}")

    # 1+3. flip ONE seed (simulates OneDrive/user edit), other stays skipped
    seed(demo / "costdb_seed_a.json", [item(1, unique_a), item(3, "ГамаТрети Кс789")])
    t0 = time.time()
    s2 = cdb.sync_json_sources(demo, db)
    dt1 = time.time() - t0
    st = cdb.stats(db)
    check("skipped source survives a changed-sibling sync (prune fix)", st["cost_items"] == 3,
          f"items={st['cost_items']}")
    check("re-import + skip reported honestly", s2["added"] == 2 and s2["skipped"] == 1,
          f"added={s2['added']} skipped={s2['skipped']}")

    # 4. changed content reachable through FTS (count parity — the index-repair invariant)
    con = cdb.connect(db)
    n_items = con.execute("SELECT COUNT(*) c FROM cost_items").fetchone()["c"]
    n_fts = con.execute("SELECT COUNT(*) c FROM cost_items_fts").fetchone()["c"]
    con.close()
    check("changed seed re-import keeps FTS in parity", n_items == n_fts == 3, f"items={n_items} fts={n_fts}")

    # 2. real disappearance still prunes
    (demo / "costdb_seed_b.json").unlink()
    s3 = cdb.sync_json_sources(demo, db)
    st = cdb.stats(db)
    check("removed file still prunes its source", st["cost_items"] == 2 and s3.get("pruned") == ["imported_b"],
          f"items={st['cost_items']} pruned={s3.get('pruned')}")

    # 5. no-op sync: fast and stable
    t0 = time.time()
    s4 = cdb.sync_json_sources(demo, db)
    dt2 = time.time() - t0
    st = cdb.stats(db)
    check("no-op sync keeps corpus intact", st["cost_items"] == 2 and s4["added"] == 0 and s4["skipped"] == 1,
          f"items={st['cost_items']} added={s4['added']} skipped={s4['skipped']}")
    check("no-op sync is cheap (no FTS rebuild)", dt2 < max(dt1 * 0.9, 0.05),
          f"no-op {dt2:.3f}s vs rebuild {dt1:.3f}s")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== verdict:", "ALL PASS" if not failures else f"{len(failures)} FAILURES: {failures}")
sys.exit(1 if failures else 0)
