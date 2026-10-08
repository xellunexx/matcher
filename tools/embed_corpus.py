# -*- coding: utf-8 -*-
"""One-time: embed every cost row with bge-m3 -> corpus_emb.npy + corpus_meta.json.
Re-run only when the corpus changes. Needs sentence-transformers (+ CUDA torch for speed)."""
import json, sqlite3, sys, time
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

import _paths
HERE = _paths.WORK
DB = str(_paths.DB)
MODEL = _paths.BGE_M3

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = [dict(r) for r in conn.execute(
    "SELECT id, desc, unit, amount_eur, origin_ref, origin_kind, status, source_key, code "
    "FROM cost_items WHERE status != 'retired'")]
print("rows", len(rows), flush=True)
import torch
m = SentenceTransformer(MODEL, device="cuda" if torch.cuda.is_available() else "cpu")
m.max_seq_length = 128
t0 = time.time()
emb = m.encode([r["desc"] for r in rows], batch_size=64, normalize_embeddings=True,
               show_progress_bar=True, convert_to_numpy=True).astype(np.float16)
np.save(HERE / "corpus_emb.npy", emb)
(HERE / "corpus_meta.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
print(f"done {len(rows)} rows in {time.time() - t0:.0f}s", flush=True)
