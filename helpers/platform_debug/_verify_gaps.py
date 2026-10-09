# -*- coding: utf-8 -*-
"""Verify each awaitingHuman row of an estimation pack against OLD tenderops data/demo.

Price-evidence surfaces checked:
  1. canonical corpus incl. pending_review (SQLite, include_pending=True)
  2. old demo priced BoQ pack: demo/boq_601701.json (129 unitEur rows)
  3. old submission workbooks: 02_КСС_остойностена.xlsx, 03_Анализи_на_единични_цени.xlsx
  4. user price workbook: data/_inve_prices.xlsx
  5. estimation_prices.json is zzzzzen-side only — old demo has none.

Matching = engine's own machinery: costdb._norm_unit + pipeline.exact_norm/tokens.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ZEN = Path(r"C:\lab\tenderops\zzzzzen")
OLD = Path(r"C:\lab\tenderops\tenderops\data")


def mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ZEN / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


cdb = mod("costdb")
pipe = mod("pipeline")

PACK = ZEN / "data" / "demo" / "estimation_inbox" / "estimation_EST20261003_010811.json"
DB = ZEN / "tenderops.sqlite3"

pack = json.loads(PACK.read_text(encoding="utf-8-sig"))
gaps = [l for l in pack["boq"] if l.get("rule") == "EST"]
print(f"gaps: {len(gaps)}")

# ---- build evidence index from old demo sources -----------------------------
evidence = []  # (exact_norm_desc, unit_norm, price_eur, currency_note, origin_label)


def add_ev(desc, unit, eur, label):
    if not desc or not isinstance(eur, (int, float)) or eur <= 0:
        return
    evidence.append((cdb.exact_norm(str(desc)), cdb._norm_unit(unit), float(eur), label))


# 2) boq_601701.json — priced tender rows (OBSERVED tender prices)
for l in json.loads((OLD / "demo" / "boq_601701.json").read_text(encoding="utf-8-sig")):
    add_ev(l.get("desc"), l.get("unit"), l.get("unitEur"), f"boq_601701:{l.get('key')}")

# 3) submission workbooks — priced КСС + анализи
import openpyxl  # noqa: E402

for fn in ("02_КСС_остойностена.xlsx", "03_Анализи_на_единични_цени.xlsx"):
    p = OLD / "demo" / "processed" / "submission_601701" / fn
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            vals = [v for v in row]
            texts = [str(v).strip() for v in vals if isinstance(v, str) and len(str(v).strip()) >= 12]
            nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            if not texts or len(nums) < 2:
                continue
            desc = max(texts, key=len)
            if pipe._is_header_low(desc.lower()):
                continue
            # unit = any cell normalizing as unit
            unit = next((str(v).strip() for v in vals if isinstance(v, str) and cdb._norm_unit(v)), None)
            add_ev(desc, unit, nums[-1] if len(nums) >= 2 else None, f"{fn}:{ws.title}")
    wb.close()

# 4) _inve_prices.xlsx (xlsx content despite .xls-era name)
p = OLD / "_inve_prices.xlsx"
wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
for ws in wb.worksheets:
    for row in ws.iter_rows(values_only=True):
        vals = list(row)
        texts = [str(v).strip() for v in vals if isinstance(v, str) and len(str(v).strip()) >= 12]
        nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not texts or not nums:
            continue
        desc = max(texts, key=len)
        if pipe._is_header_low(desc.lower()):
            continue
        unit = next((str(v).strip() for v in vals if isinstance(v, str) and cdb._norm_unit(v)), None)
        add_ev(desc, unit, nums[-1], f"_inve_prices:{ws.title}")
wb.close()

print(f"evidence index: {len(evidence)} priced rows from old demo")


def ev_match(desc, unit):
    """Best old-demo evidence for (desc, unit): exact_norm equality, else token overlap >= 0.7.
    Unit must match when both sides carry a known unit."""
    dn = cdb.exact_norm(desc)
    dt = cdb.tokens(desc)
    un = cdb._norm_unit(unit)
    exact, fuzzy = [], []
    for en, eu, eur, label in evidence:
        if un and eu and eu != un:
            continue
        if en == dn:
            exact.append((eur, label, 1.0))
            continue
        et = cdb.tokens(en)
        if not dt or not et:
            continue
        ov = len(dt & et) / max(len(dt | et), 1)
        if ov >= 0.7:
            fuzzy.append((eur, label, ov))
    exact.sort(key=lambda x: -x[2])
    fuzzy.sort(key=lambda x: -x[2])
    return (exact or fuzzy or [None])[0]


# ---- per-item verdicts -------------------------------------------------------
found, notfound = [], []
for l in gaps:
    desc, unit = l["desc"], l.get("unit")
    # 1) corpus incl. pending_review — engine same machinery, wider status window
    cands = cdb.search_candidates(str(DB), desc, unit, limit=3, include_pending=True)
    corpus = cands[0] if cands else None
    # 2) old-demo evidence
    ev = ev_match(desc, unit)
    if corpus or ev:
        found.append({"row": l["row"], "desc": desc, "unit": unit, "qty": l["qty"],
                      "corpus": ({"src": corpus["source_key"], "status": corpus["status"],
                                  "eur": corpus["amount_eur"], "ref": corpus.get("origin_ref")}
                                 if corpus else None),
                      "old_demo": ({"eur": ev[0], "from": ev[1], "score": round(ev[2], 2)}
                                   if ev else None)})
    else:
        notfound.append({"row": l["row"], "desc": desc, "unit": unit, "qty": l["qty"]})

print(f"\n=== AVAILABLE SOMEWHERE: {len(found)} ===")
for f in found:
    print(f"R{f['row']} {f['desc'][:60]} | {f['unit']} x{f['qty']}")
    if f["corpus"]:
        c = f["corpus"]
        print(f"   corpus: {c['src']} [{c['status']}] {c['eur']} EUR | {str(c['ref'])[:60]}")
    if f["old_demo"]:
        e = f["old_demo"]
        print(f"   old demo: {e['eur']} EUR | {e['from']} (match {e['score']})")
print(f"\n=== TRULY NOT AVAILABLE: {len(notfound)} ===")
for f in notfound:
    print(f"R{f['row']} {f['desc'][:70]} | {f['unit']} x{f['qty']}")

out = {"pack": PACK.name, "gaps": len(gaps), "available": found, "not_available": notfound}
(ZEN / "_verify_gaps_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nwrote _verify_gaps_result.json")
