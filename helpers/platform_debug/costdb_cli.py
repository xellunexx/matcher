#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TenderOps cost DB CLI. JSON is import/export only; SQLite is canonical."""
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
import costdb

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["sync", "stats", "sources", "export", "search"])
    ap.add_argument("--demo-dir", default="data/demo")
    ap.add_argument("--db", default=os.environ.get("TENDEROPS_DB_PATH", "tenderops.sqlite3"))
    ap.add_argument("--out")
    ap.add_argument("--query")
    ap.add_argument("--unit")
    ap.add_argument("--code")
    ap.add_argument("--include-pending", action="store_true")
    args = ap.parse_args()
    db = Path(args.db).resolve()
    if args.command == "sync":
        print(json.dumps(costdb.sync_json_sources(args.demo_dir, db), ensure_ascii=False, indent=2))
    elif args.command == "stats":
        print(json.dumps(costdb.stats(db), ensure_ascii=False, indent=2))
    elif args.command == "sources":
        print(json.dumps(costdb.source_registry(db), ensure_ascii=False, indent=2))
    elif args.command == "export":
        if not args.out:
            ap.error("export requires --out")
        n = costdb.export_cost_items(db, args.out, include_pending=args.include_pending)
        print(f"exported {n} rows -> {args.out}")
    elif args.command == "search":
        if not args.query and not args.code:
            ap.error("search requires --query or --code")
        print(json.dumps(costdb.search_candidates(db, args.query or "", args.unit, code=args.code, include_pending=args.include_pending), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
