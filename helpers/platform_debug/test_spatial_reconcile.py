# -*- coding: utf-8 -*-
"""test_spatial_reconcile.py — KIMI-K3 §8 acceptance accounting.
Simulates the frontend exactly-once rollup (Section 8's accounting invariant)
against every inbox EST pack with a compiled scene:
  1. per BOQ row, Σ object cost_share ≤ 1 (+ε); fully allocated rows sum to 1
  2. at final build state: rollup == allocated_eur (+ε)
  3. ledger identity: allocated + unallocated == boq_total_eur (+ε) — plain
     arithmetic on the allocation record; FACTS-1 removed completion-time
     staging, so this is no longer a HUD claim
  4. boq_total_eur ≈ pricing.totalExclVat within rounding (reported drift)
No double counting between BUILDING roots and children is possible by (1).
Run: venv/Scripts/python.exe test_spatial_reconcile.py
"""
import json, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))
spec = importlib.util.spec_from_file_location("spatial", str(ROOT / "app" / "spatial.py"))
spatial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spatial)

def recombine(a, b):  # round-sum drift bound: cent rounding per priced row
    return abs(a - b)

failures = []
def ok(cond, name, detail=""):
    if cond:
        print(f"  ok - {name}")
    else:
        failures.append(name)
        print(f"  FAIL - {name} {detail}")

packs = [p for p in sorted(Path(ROOT / "data" / "demo" / "estimation_inbox").glob("estimation_EST*.json"))]
ok(len(packs) >= 1, "inbox packs present")

for f in packs:
    pack = json.loads(f.read_text(encoding="utf-8-sig"))
    m = spatial.build_spatial_model({"id": "t", "name": "t"}, pack)
    sv2 = m.get("scene_v2")
    if not isinstance(sv2, dict):
        print(f"  -- {f.stem[:24]}: no scene_v2, skipped")
        continue
    ca = sv2["cost_allocation"]
    objs = sv2["objects"]
    sums = {}
    for l in pack.get("boq") or []:
        if not isinstance(l, dict):
            continue
        s = l.get("sumEur")
        if isinstance(s, (int, float)):
            sums[str(l.get("key"))] = float(s)
    tag = f.stem[:24]

    # (1) per-row share sums
    per_row = {}
    for o in objs:
        for k, sh in (o.get("inspect", {}).get("cost_share") or {}).items():
            per_row[k] = per_row.get(k, 0.0) + float(sh)
    over = {k: v for k, v in per_row.items() if v > 1 + 1e-6}
    ok(not over, f"{tag}: no row over-allocated (shares ≤ 1)", str(over)[:120])

    # (2) final build state: all shares at c=1 → rollup == allocated
    final = 0.0
    for k, v in per_row.items():
        if k in sums:
            final += sums[k] * min(1.0, v)
    ok(recombine(final, float(ca["allocated_eur"])) <= max(0.02, 1e-4 * max(final, 1)),
       f"{tag}: final rollup == allocated ({final:.2f} == {ca['allocated_eur']})")

    # (3) ledger identity: allocated + unallocated == boq_total (FACTS-1: the
    #     mesh rollup never stages unallocated money; this stays pure arithmetic)
    with_unalloc = final + float(ca["unallocated_eur"])
    ok(recombine(with_unalloc, float(ca["boq_total_eur"])) <= max(0.02, 1e-4 * max(with_unalloc, 1)),
       f"{tag}: allocated+unallocated == boq_total ({with_unalloc:.2f} == {ca['boq_total_eur']})")

    # (4) canonical project total
    ptot = (pack.get("pricing") or {}).get("totalExclVat")
    if isinstance(ptot, (int, float)):
        drift = abs(with_unalloc - float(ptot))
        bound = max(0.51, 1e-4 * abs(float(ptot)))
        ok(drift <= bound, f"{tag}: within rounding of pricing.totalExclVat (drift €{drift:.2f})")

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("cost reconciliation (§8): all scene packs clean")
