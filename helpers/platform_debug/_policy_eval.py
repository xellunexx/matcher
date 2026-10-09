# Policy evaluation harness: parse Hisarya KCC once, run match_cost_v2 once per
# row, then apply each allocation POLICY as a pure filter over the same evidence.
# Policies share the evidence pass → differences come from the rule, nothing else.
import json, importlib.util
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
XLS = Path(r"C:\lab\tenderops\20261002_160236_КС -  Хисаря-всички части - за Възложителя.xls")
DB = ROOT / "tenderops.sqlite3"
STORE = ROOT / "data/demo/estimation_prices.json"
OUT = ROOT / "_policy_eval_report.json"
FLOOR = 55.0          # matcher's own minimum-acceptance bar, reused for bulk
OUTLIER_X = 10.0      # line total > X × median priced-line → demoted to review

def _mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

cdb, pp = _mod("costdb"), _mod("pipeline")

# ---- evidence pass (once) ---------------------------------------------------
rows = pp.parse_kss_priced(XLS)
raw_store = json.loads(STORE.read_text(encoding="utf-8-sig")) if STORE.exists() else {}
# CLEAN STORE: only true human entries — bulk-adopted entries are policy OUTPUT,
# not evidence; counting them would launder each policy into the baseline.
store = {k: v for k, v in raw_store.items()
         if (v or {}).get("actor") not in ("operator:bulk",)}
print(f"rows={len(rows)} | store entries: raw={len(raw_store)} clean(human-only)={len(store)}")

ev = []  # per row: (row, store_price|None, best|None, top_cands)
for i, r in enumerate(rows):
    mk = (r.get("desc") or "").strip().lower() + " ∥ " + (r.get("unit") or "бр").strip()
    if mk in store:
        ev.append((r, float(store[mk]["price"]), None, []))
        continue
    best, score, evd = pp.match_cost_v2(r, str(DB))
    ev.append((r, None, best, (evd or {}).get("top_candidates") or []))
    if i % 200 == 0:
        print(f"  matched {i}/{len(rows)}")
print("evidence pass done")

# ---- policies ----------------------------------------------------------------
def sc(c): return float((c or {}).get("score") or 0)
def st(c): return (c or {}).get("status")

def run_policy(name, rule, demote_outliers=False):
    """rule(best, top) -> 'committed' | 'provisional' | 'open'."""
    committed = []   # (line_sum, status, score)
    provisional = [] # (line_sum, status, score)
    open_rows = 0
    for r, sprice, best, cands in ev:
        qty = float(r.get("qty") or 0)
        top = cands[0] if cands else None
        if sprice is not None:
            committed.append((sprice * qty, "operator", 999.0)); continue
        verdict = rule(best, top)
        c = best or top
        if verdict == "committed" and c is not None:
            committed.append((float(c.get("unitEur") or 0) * qty, st(c), sc(c)))
        elif verdict == "provisional" and top and top.get("unitEur"):
            provisional.append((float(top["unitEur"]) * qty, st(top), sc(top)))
        else:
            open_rows += 1
    sums = sorted(s for s, _, _ in committed if s > 0)
    thr = (sums[len(sums) // 2] * OUTLIER_X) if sums else 0.0
    flagged = [x for x in committed if thr and x[0] > thr]
    if demote_outliers:
        committed = [x for x in committed if x not in flagged]
        provisional = provisional + flagged
        flagged_after = 0, 0.0
    else:
        flagged_after = len(flagged), sum(x[0] for x in flagged)
    ctot = sum(x[0] for x in committed)
    hist = Counter(int(x[2] // 10) * 10 for x in committed if x[1] != "operator")
    phist = Counter(int(x[2] // 10) * 10 for x in provisional)
    return {"policy": name,
            "committed_rows": len(committed), "committed_EUR": round(ctot, 2),
            "flagged_lines": flagged_after[0], "flagged_EUR": round(flagged_after[1], 2),
            "committed_minus_flagged": round(ctot - flagged_after[1], 2),
            "pending_in_committed": sum(1 for x in committed if x[1] == "pending_review"),
            "provisional_rows": len(provisional),
            "provisional_EUR": round(sum(x[0] for x in provisional), 2),
            "provisional_score_hist": dict(sorted(phist.items())),
            "open": open_rows,
            "adopted_score_hist": dict(sorted(hist.items()))}

policies = [
    ("B_floor_only",  # review-queue world: only matcher + human store commit
     lambda best, top: "committed" if best else "open", False),
    ("A_tiered",      # bulk-adopt active tops>=floor; pending tops -> provisional
     lambda best, top: "committed" if best else
        "committed" if (top and st(top) == "active" and sc(top) >= FLOOR) else
        "provisional" if (top and st(top) == "pending_review") else "open", False),
    ("C_any_status_floor",  # single bulk, floor for ANY status
     lambda best, top: "committed" if best else
        "committed" if (top and sc(top) >= FLOOR) else "open", False),
    ("D_adopt_all",   # old behaviour: first candidate unconditionally
     lambda best, top: "committed" if best else
        "committed" if top else "open", False),
    ("E_hybrid_A_demote",  # A + flagged lines demoted OUT of committed
     lambda best, top: "committed" if best else
        "committed" if (top and st(top) == "active" and sc(top) >= FLOOR) else
        "provisional" if (top and st(top) == "pending_review") else "open", True),
]

results = [run_policy(n, f, d) for n, f, d in policies]
OUT.write_text(json.dumps({"rows": len(rows), "floor": FLOOR, "outlier_x": OUTLIER_X,
                           "store_entries_used": len(store), "results": results},
                          ensure_ascii=False, indent=1), encoding="utf-8")
for r in results:
    print(f"\n{r['policy']:20s} committed {r['committed_EUR']:>14,.2f}E "
          f"({r['committed_rows']}r, {r['pending_in_committed']} pend) | "
          f"flagged {r['flagged_lines']}={r['flagged_EUR']:>12,.0f} | "
          f"defensible {r['committed_minus_flagged']:>14,.2f} | "
          f"provisional {r['provisional_EUR']:>12,.0f} ({r['provisional_rows']}r) | open {r['open']}")
    print(f"{'':20s} adopted-score hist {r['adopted_score_hist']}")
    print(f"{'':20s} provisional hist   {r['provisional_score_hist']}")
print(f"\nreport -> {OUT}")
