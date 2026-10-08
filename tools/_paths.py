# -*- coding: utf-8 -*-
"""Repo-relative paths shared by every tool. Import this first."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICEDB = ROOT / "pricedb"            # costdb_seed*.json  -> the price corpus (source of truth)
KCC_PAIRS = ROOT / "kcc" / "pairs"    # blank KCC + the same KCC priced by the operator
KCC_UNPRICED = ROOT / "kcc" / "unpriced"
WORK = ROOT / "work"                  # generated: sqlite db, embeddings, caches, reports (gitignored)
DB = WORK / "tenderops.sqlite3"
WORK.mkdir(exist_ok=True)

# bge-m3 embedding model: local folder or the HF id (downloaded on first use)
BGE_M3 = os.environ.get("BGE_M3_PATH", "BAAI/bge-m3")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))          # -> `from app import matcher, costdb, pipeline`
if str(ROOT / "app") not in sys.path:
    sys.path.append(str(ROOT / "app"))     # -> `import matcher` (stdlib-only module)

# Blank KCC -> operator-priced twin. The priced file's rows are the ground truth.
PAIRS = [
    ("КСС(56517911).xlsx", "КСС_НЧ_Своге_КОЛЕВ ГРУП СТРОЙ 2001~(56903555).xlsx"),
    ("КСС(40023554).xlsx", "КСС(40023554)(40500727).xlsx"),
    ("КСС трафопост(48440043).xlsx", "КСС трафопост(48440043)(48763366).xlsx"),
    ("КСС към ценово предложение(57123673).xls", "КСС към ценово предложение(57123673)(57409050).xls"),
    ("4. КСС оферта -2026(56440729).xls", "4. КСС оферта -2026(56720082).xls"),
]
