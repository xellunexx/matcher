# -*- coding: utf-8 -*-
"""Hidden-twin eval: meaning retrieval (bge-m3) + deterministic gates + price consensus.

Per line: embed -> nearest corpus rows by meaning (twin tender hidden) ->
drop rows whose unit dimension, declared spec or action family contradicts ->
commit only when the surviving close neighbours agree on price.
No LLM. Run after tools/embed_corpus.py and tools/kcc_io.py."""
import json, sys, time, statistics
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

import _paths
HERE = _paths.WORK
import matcher as M  # stdlib-only vendored matcher: units, specs, concepts

SIM_MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 0.80   # neighbour must mean ~the same
BAND = float(sys.argv[2]) if len(sys.argv) > 2 else 0.03      # neighbours within this of the best
K = 30
EURBGN = 1.95583
# OWN=1: only the company's own priced rows (operator_pricelist from OTHER tenders),
# i.e. "what did WE charge for this kind of work before".
OWN = len(sys.argv) > 3 and sys.argv[3] == "own"

emb = np.load(HERE / "corpus_emb.npy").astype(np.float32)
meta = json.loads((HERE / "corpus_meta.json").read_text(encoding="utf-8"))
lines = json.loads((HERE / "eval_lines.json").read_text(encoding="utf-8"))

t0 = time.time()
m = SentenceTransformer(_paths.BGE_M3, device="cuda" if __import__("torch").cuda.is_available() else "cpu")
m.max_seq_length = 128
q = m.encode([l["text"] for l in lines], batch_size=64, normalize_embeddings=True, convert_to_numpy=True)
t_embed = time.time() - t0

ACT = M._ACTION_CONCEPTS


def actions(t):
    return set(M.canonical_tokens(t)) & ACT


def gate(line, row):
    """Hard contradictions only; silence never rejects."""
    f = M.unit_rate_factor(row["unit"], line["unit"])
    if f is None and M.units_compatible(row["unit"], line["unit"]) is not True:
        return None, "unit"
    if M.spec_conflicts(line["text"], row["desc"]):
        return None, "spec"
    qa, ca = actions(line["text"]), actions(row["desc"])
    if qa and ca and not (qa & ca):
        return None, "action"
    return float(f or 1), None


stats = {"lines": len(lines), "committed": 0, "commit_ok": 0, "commit_wrong": 0, "review": 0,
         "none": 0, "top1_within15": 0}
report = []
sims_all = q @ emb.T
for li, line in enumerate(lines):
    sims = sims_all[li]
    order = np.argsort(-sims)[: K * 3]
    keep = []
    for j in order:
        row = meta[j]
        if row["origin_ref"] == line["hide"]:
            continue
        if OWN and row["origin_kind"] != "operator_pricelist":
            continue
        f, why = gate(line, row)
        if f is None:
            continue
        keep.append((float(sims[j]), row["amount_eur"] * f, row))
        if len(keep) >= K:
            break
    truth = line["truth"]
    ok = lambda p: abs(p - truth) <= truth * 0.15 or abs(p * EURBGN - truth) <= truth * 0.15
    verdict, price = "none", None
    if keep and keep[0][0] >= SIM_MIN:
        best = keep[0][0]
        band = [x for x in keep if x[0] >= best - BAND]
        prices = [x[1] for x in band]
        med = statistics.median(prices)
        agree = [p for p in prices if abs(p - med) <= med * 0.15]
        if len(agree) / len(prices) >= 0.6:
            verdict, price = "match", statistics.median(agree)
        else:
            verdict = "review"
    if keep and ok(keep[0][1]):
        stats["top1_within15"] += 1
    if verdict == "match":
        stats["committed"] += 1
        stats["commit_ok" if ok(price) else "commit_wrong"] += 1
    else:
        stats[verdict] += 1
    report.append({"text": line["text"], "unit": line["unit"], "truth": truth, "verdict": verdict,
                   "price": price, "top": [(round(s, 3), round(p, 2), r["desc"][:80], r["unit"])
                                          for s, p, r in keep[:5]]})

stats["seconds_per_kcc_331_lines"] = round(time.time() - t0, 1)
stats["embed_seconds"] = round(t_embed, 1)
print(json.dumps(stats, ensure_ascii=False))
print("baseline current matcher (twin hidden): committed 43, within15 9")
(HERE / f"eval_embed_{SIM_MIN}_{BAND}{'_own' if OWN else ''}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
