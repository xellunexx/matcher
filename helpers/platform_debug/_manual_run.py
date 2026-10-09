# Manual run: parse EVERY КС position in the Hisarya XLS and match each against
# ALL price evidence under data/demo (active + pending + operator store).
# No app, no HTTP — direct pipeline + costdb calls. Loud output.
import json, sys, importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
XLS = Path(r"C:\lab\tenderops\20261002_160236_КС -  Хисаря-всички части - за Възложителя.xls")
DB = ROOT / "tenderops.sqlite3"
STORE = ROOT / "data/demo/estimation_prices.json"
OUT = ROOT / "_manual_run_report.json"

def _mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

cdb, pp = _mod("costdb"), _mod("pipeline")

store = json.loads(STORE.read_text(encoding="utf-8-sig")) if STORE.exists() else {}

# ---- 1. parse the file through the same engine path (parse_kss_priced already
# scopes to the Обобщена summary sheet — detail sheets duplicate the same work) ----
rows = pp.parse_kss_priced(XLS)
scope_rows = rows
print(f"parsed {len(rows)} priced-format rows (Обобщена scope)")
from collections import Counter
print("sections:", dict(Counter(r.get("section") for r in rows)))

def mkey(r):
    return (r.get("desc") or "").strip().lower() + " ∥ " + (r.get("unit") or "бр").strip()

def classify(r):
    """Returns (tier, adopted_price_or_None, adopted_cand_or_None, candidates)."""
    mk = mkey(r)
    if mk in store:
        e = store[mk]
        return "STORE", float(e["price"]), {"desc": e.get("desc"), "status": "operator",
                "source": e.get("note"), "ref": e.get("at")}, []
    best, score, ev = pp.match_cost_v2(r, str(DB))
    cands = (ev or {}).get("top_candidates") or []
    if best is not None:
        return "AUTO", float(best.get("unitEur") or 0), best, cands
    if cands:
        return "CANDIDATE", None, None, cands
    return "MISSING", None, None, cands

report_rows, stats = [], {"STORE": 0, "AUTO": 0, "CANDIDATE": 0, "MISSING": 0}
tot = {"STORE": 0.0, "AUTO": 0.0}
ceiling_act = ceiling_pend = 0.0

for r in scope_rows:
    tier, price, adopted, cands = classify(r)
    stats[tier] += 1
    qty = float(r.get("qty") or 0)
    rec = {"key": f"{r.get('section')}|{r.get('row')}", "desc": r.get("desc"),
           "unit": r.get("unit"), "qty": qty, "tier": tier}
    if price is not None:
        rec["unitEur"] = price; rec["sumEur"] = round(qty * price, 2)
        tot[tier] += qty * price
        if adopted: rec["adopted"] = adopted
    if cands:
        top = cands[0]
        rec["top_candidate"] = {k: top.get(k) for k in
                                ("id", "desc", "unit", "score", "source", "ref", "status", "unitEur")}
        if tier == "CANDIDATE" and top.get("unitEur"):
            if top.get("status") == "active":
                ceiling_act += qty * float(top["unitEur"])
            else:
                ceiling_pend += qty * float(top["unitEur"])
        rec["n_candidates"] = len(cands)
    report_rows.append(rec)

committed = tot["STORE"] + tot["AUTO"]
print("\n=== TIER COUNTS (Обобщена scope) ===")
print(json.dumps(stats, ensure_ascii=False))
print(f"committed (store+auto):  {committed:,.2f} €")
print(f"  of which store/manual: {tot['STORE']:,.2f} €")
print(f"  of which auto-matched: {tot['AUTO']:,.2f} €")
print(f"ceiling + top active candidates on gaps:  {committed + ceiling_act:,.2f} €")
print(f"ceiling + any top candidate on gaps:      {committed + ceiling_act + ceiling_pend:,.2f} €")

OUT.write_text(json.dumps({"stats": stats,
                           "totals": {"committed": round(committed, 2),
                                      "store": round(tot["STORE"], 2),
                                      "auto": round(tot["AUTO"], 2),
                                      "gap_top_active": round(ceiling_act, 2),
                                      "gap_top_pending": round(ceiling_pend, 2)},
                           "rows": report_rows}, ensure_ascii=False, indent=1,
                          default=lambda o: sorted(o) if isinstance(o, set) else str(o)),
                  encoding="utf-8")
print(f"\nreport -> {OUT}")
