#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import tempfile, json, os
import costdb
from pipeline_sqlite_v2 import match_cost_v2

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "_costdb_test_demo"
DEMO.mkdir(exist_ok=True)
# use existing SEK seed if present
src = ROOT / "costdb_seed_sek_2026.json"
if not src.exists():
    alt = ROOT / "data" / "demo" / "costdb_seed_sek_2026.json"
    if alt.exists():
        src = alt
if not src.exists():
    raise SystemExit("costdb_seed_sek_2026.json not found in project root or data/demo")
(DEMO / src.name).write_bytes(src.read_bytes())

dbpath = DEMO / "test.sqlite3"
print(costdb.sync_json_sources(DEMO, dbpath, force=True))
print(costdb.stats(dbpath))
first = costdb.get_active_rows(dbpath)[0]
row = {"desc": first["desc"], "unit": first["unit"], "qty":1, "code": first.get("code") or ""}
best, score, ev = match_cost_v2(row, dbpath)
print("MATCH", best and {"id":best["id"],"ref":best["ref"],"unitEur":best["unitEur"]}, score, ev)
assert best is not None, "expected a deterministic match in SEK seed"
print("SELFTEST OK")
