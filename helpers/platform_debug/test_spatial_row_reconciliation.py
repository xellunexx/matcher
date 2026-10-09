# -*- coding: utf-8 -*-
"""test_spatial_row_reconciliation.py — KIMI-K3 §4a.F/G/H regression.
For every estimation pack in the inbox, rebuild the spatial model offline and
assert the pack.boq -> model.elements reconciliation contract:
  - every finite positive-qty BOQ row survives into elements (or is OUT_OF_SCOPE)
  - excluded_rows is machine-readable; MISSING_IN_ELEMENTS must never occur
  - no duplicate element ids (stable keys)
  - scene_v2 (when present) exposes cost_allocation.row_categories, and every
    object's boq_keys resolve in it
  - cost allocation still reconciles: boq_total = allocated + unallocated
Run: venv/Scripts/python.exe test_spatial_row_reconciliation.py
"""
import json, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

spec = importlib.util.spec_from_file_location("spatial", str(ROOT / "app" / "spatial.py"))
spatial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spatial)

failures = []
def ok(cond, name, detail=""):
    if cond:
        print(f"  ok - {name}")
    else:
        failures.append(name)
        print(f"  FAIL - {name} {detail}")

packs = sorted(Path(ROOT / "data" / "demo" / "estimation_inbox").glob("estimation_EST*.json"))
ok(len(packs) >= 1, f"inbox has packs ({len(packs)})")

for f in packs:
    pack = json.loads(f.read_text(encoding="utf-8-sig"))
    boq = [l for l in (pack.get("boq") or []) if isinstance(l, dict)]
    pos = 0
    for l in boq:
        try:
            q = float(l.get("qty") or 0.0)
        except Exception:
            q = 0.0
        if q == q and q > 0:
            pos += 1

    m = spatial.build_spatial_model({"id": "test", "name": "test"}, pack)
    els = m.get("elements") or []
    rec = m.get("quantity_reconciliation") or {}
    tag = f.stem[:26]

    ok(rec.get("boq_rows_total") == len(boq), f"{tag}: boq_rows_total matches pack", f"({rec.get('boq_rows_total')} vs {len(boq)})")
    ok(rec.get("positive_qty_rows") == pos, f"{tag}: positive_qty_rows matches independent recount", f"({rec.get('positive_qty_rows')} vs {pos})")
    ok(rec.get("elements_rows") == len(els), f"{tag}: elements_rows matches model elements")

    excluded = rec.get("excluded_rows") or []
    reasons = {x.get("reason") for x in excluded}
    ok(reasons <= {"OUT_OF_SCOPE", "INVALID_QUANTITY", "UNSUPPORTED_UNIT", "HEADER_ROW", "DUPLICATE_SOURCE_ROW"},
       f"{tag}: exclusion reasons machine-readable", f"({reasons})")
    ok(not any(x.get("reason") == "MISSING_IN_ELEMENTS" for x in excluded),
       f"{tag}: no positive row silently missing (no MISSING_IN_ELEMENTS)")
    # with no scope filter, OUT_OF_SCOPE must not appear
    ok(not any(x.get("reason") == "OUT_OF_SCOPE" for x in excluded), f"{tag}: no OUT_OF_SCOPE without scope_id")
    ok(rec.get("duplicate_element_ids") == 0, f"{tag}: no duplicate element ids")

    # positive rows all present by stable key (independent check)
    el_keys = {str(e.get("id")) for e in els}
    missing = []
    for l in boq:
        try:
            q = float(l.get("qty") or 0.0)
        except Exception:
            q = 0.0
        if q == q and q > 0 and str(l.get("key")) not in el_keys:
            missing.append(l.get("key"))
    ok(not missing, f"{tag}: all positive rows keyed into elements", f"missing={missing[:5]}")

    # scene_v2 row categories
    sv2 = m.get("scene_v2")
    if isinstance(sv2, dict) and sv2.get("cost_allocation"):
        ca = sv2["cost_allocation"]
        rc = ca.get("row_categories")
        ok(isinstance(rc, dict) and len(rc) > 0, f"{tag}: row_categories exposed ({len(rc) if isinstance(rc, dict) else 0})")
        dangling = []
        for o in (sv2.get("objects") or []):
            for k in (o.get("boq_keys") or []):
                if rc is not None and k not in rc:
                    dangling.append(k)
        ok(not dangling, f"{tag}: every object boq_key has a row category")
        tot = float(ca.get("boq_total_eur") or 0.0)
        parts = float(ca.get("allocated_eur") or 0.0) + float(ca.get("unallocated_eur") or 0.0)
        ok(abs(tot - parts) < 0.01, f"{tag}: boq_total == allocated + unallocated", f"({tot} vs {parts})")

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("row reconciliation: all packs clean")
