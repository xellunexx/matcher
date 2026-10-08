# -*- coding: utf-8 -*-
"""Build work/tenderops.sqlite3 (cost_items + FTS5 index) from pricedb/costdb_seed*.json.
Same importer the product uses (app/costdb.py: sync_json_sources). Idempotent (sha-keyed)."""
import json
import _paths
from app import costdb

stats = costdb.sync_json_sources(_paths.PRICEDB, _paths.DB, force=True)
print(json.dumps({k: v for k, v in stats.items() if k != "sources"}, ensure_ascii=False))
for s in stats["sources"]:
    print(f"  {s['rows']:6d}  {s['source']}")
print("db ->", _paths.DB, "|", costdb.stats(_paths.DB))
