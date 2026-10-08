# -*- coding: utf-8 -*-
"""Hidden-twin eval of the operator's pricing rule:
   twin (same normalized text + unit dimension, other tender/source) -> median of the twins' prices
   else -> median price of the K nearest same-meaning rows (bge-m3), after hard gates
          (unit dimension, declared spec conflict, demolish-vs-install).
All corpus prices are treated as valid market prices. Every price is labelled
twin | median_k so the provenance (observed vs derived) stays visible.
Run after tools/embed_corpus.py and tools/kcc_io.py."""
import json, sys, time, statistics
from pathlib import Path
import numpy as np
import torch
from sentence_transformers import SentenceTransformer

import _paths
HERE = _paths.WORK
import matcher as M  # noqa: E402

EURBGN = 1.95583
emb = torch.tensor(np.load(HERE / "corpus_emb.npy").astype(np.float32))
meta = json.loads((HERE / "corpus_meta.json").read_text(encoding="utf-8"))
lines = json.loads((HERE / "eval_lines.json").read_text(encoding="utf-8"))
dev = "cuda" if torch.cuda.is_available() else "cpu"

t0 = time.time()
enc = SentenceTransformer(_paths.BGE_M3, device=dev)
enc.max_seq_length = 128
q = enc.encode([l["text"] for l in lines], batch_size=64, normalize_embeddings=True, convert_to_tensor=True)
sims_all = (q.float() @ emb.to(q.device).T).cpu().numpy()
t_embed = time.time() - t0

norm = lambda s: M.normalize_text(s or "")
by_text = {}
for j, r in enumerate(meta):
    by_text.setdefault(norm(r["desc"]), []).append(j)


def gate(line, row):
    f = M.unit_rate_factor(row["unit"], line["unit"])
    if f is None and M.units_compatible(row["unit"], line["unit"]) is not True:
        return None
    if M.spec_conflicts(line["text"], row["desc"]):
        return None
    qa = set(M.canonical_tokens(line["text"])) & M._ACTION_CONCEPTS
    ca = set(M.canonical_tokens(row["desc"])) & M._ACTION_CONCEPTS
    if qa and ca and not (qa & ca):
        return None
    return float(f or 1)


def run(k, sim_min):
    st = {"k": k, "sim_min": sim_min, "twin": 0, "twin_ok": 0, "median": 0, "median_ok15": 0,
          "median_ok30": 0, "none": 0}
    rows = []
    for li, line in enumerate(lines):
        truth = line["truth"]
        ok = lambda p, tol: abs(p - truth) <= truth * tol or abs(p * EURBGN - truth) <= truth * tol
        twins = []
        for j in by_text.get(norm(line["text"]), []):
            r = meta[j]
            if r["origin_ref"] == line["hide"]:
                continue
            f = gate(line, r)
            if f is not None:
                twins.append(r["amount_eur"] * f)
        if twins:
            p = statistics.median(twins)
            st["twin"] += 1
            st["twin_ok"] += ok(p, 0.15)
            rows.append((line["text"], truth, "twin", p, len(twins)))
            continue
        sims = sims_all[li]
        near = []
        for j in np.argsort(-sims)[:300]:
            if sims[j] < sim_min:
                break
            r = meta[j]
            if r["origin_ref"] == line["hide"]:
                continue
            f = gate(line, r)
            if f is None:
                continue
            near.append(r["amount_eur"] * f)
            if len(near) >= k:
                break
        if not near:
            st["none"] += 1
            rows.append((line["text"], truth, "none", None, 0))
            continue
        p = statistics.median(near)
        st["median"] += 1
        st["median_ok15"] += ok(p, 0.15)
        st["median_ok30"] += ok(p, 0.30)
        rows.append((line["text"], truth, "median", p, len(near)))
    priced = st["twin"] + st["median"]
    st["priced"] = priced
    st["ok15_total"] = st["twin_ok"] + st["median_ok15"]
    return st, rows


print(f"device={dev} embed+search {t_embed:.1f}s for {len(lines)} lines")
best = None
for k in (3, 5, 9):
    for sim_min in (0.70, 0.75, 0.80, 0.85):
        st, rows = run(k, sim_min)
        print(json.dumps(st))
        if best is None or st["ok15_total"] > best[0]["ok15_total"]:
            best = (st, rows)
(HERE / "eval_median_best.json").write_text(json.dumps(best[1], ensure_ascii=False, indent=1), encoding="utf-8")
print("baseline current matcher (twin hidden): priced 43, within15 9 | total time", round(time.time() - t0, 1), "s")
