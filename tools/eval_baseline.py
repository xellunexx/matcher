# -*- coding: utf-8 -*-
"""Evaluate the CURRENT production matcher (app/pipeline.py: match_cost_v2) on the KCC pairs.

  python tools/eval_baseline.py              # twin present  (the operator priced this tender before)
  python tools/eval_baseline.py --hide-twin  # twin hidden   (a NEW tender - the real use case)

A line counts as "right" when the committed unit price is within 2% (twin mode)
or 15% (hidden mode) of the operator's real price (EUR, or BGN via 1.95583)."""
import argparse, json
import _paths
from app import pipeline, costdb
from kcc_io import eval_lines

ap = argparse.ArgumentParser()
ap.add_argument("--hide-twin", action="store_true")
a = ap.parse_args()
tol = 0.15 if a.hide_twin else 0.02

if a.hide_twin:
    _orig = costdb.search_candidates
    HIDE = {"ref": None}

    def _hidden(db_path, query, unit=None, limit=40, **kw):
        rows = _orig(db_path, query, unit, limit=limit * 3, **kw)
        return [r for r in rows if r.get("origin_ref") != HIDE["ref"]][:limit]
    costdb.search_candidates = _hidden

lines = eval_lines()
st = {"lines": len(lines), "priced": 0, "right": 0, "wrong": 0, "unpriced": 0, "why_unpriced": {}}
report = []
for ln in lines:
    if a.hide_twin:
        HIDE["ref"] = ln["hide"]
    cand, score, ev = pipeline.match_cost_v2({"desc": ln["text"], "unit": ln["unit"], "header": ln["header"], "key": "x"}, str(_paths.DB))
    t = ln["truth"]
    if cand:
        st["priced"] += 1
        p = cand["unitEur"]
        ok = abs(p - t) <= t * tol or abs(p * costdb.EURBGN - t) <= t * tol
        st["right" if ok else "wrong"] += 1
    else:
        st["unpriced"] += 1
        k = ("ambiguous " if ev.get("ambiguous") else "") + ("diluted " if ev.get("diluted") else "") + str(ev.get("method"))
        st["why_unpriced"][k] = st["why_unpriced"].get(k, 0) + 1
    report.append({"kcc": ln["kcc"], "text": ln["text"], "unit": ln["unit"], "truth": t,
                   "price": cand["unitEur"] if cand else None, "method": ev.get("method"),
                   "chosen": (cand or {}).get("desc"),
                   "top": [(c.get("score"), c.get("unitEur"), c.get("desc")) for c in (ev.get("top_candidates") or [])[:5]]})
print(json.dumps(st, ensure_ascii=False, indent=1))
out = _paths.WORK / f"eval_baseline_{'hidden' if a.hide_twin else 'twin'}.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("report ->", out)
