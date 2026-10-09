# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Golden acceptance for the KCC matching pipeline.

Runs ``scripts/kcc_golden.py`` as a subprocess (its own throwaway embedded
PostgreSQL cluster): a Bulgarian cost-database seed is ingested, the
``kcc9-test`` bill is priced through the production matching path, and the
result is compared against the human-priced ``kcc9`` gold workbook.

Heavy by design (model-boot + 400-line scoring), so it is off the default
path: set ``OE_KCC_GOLDEN=1`` to run it. The thresholds locked here are the
current proven capability - run the script standalone to see the full
per-row report CSV, and raise the floor when the pipeline improves.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND / "tests" / "fixtures" / "kcc"

#: Proven on 2026-10: 93 % of gold-priced rows within 10 %, bill total inside
#: 1 % of the human-priced gold. The floor is a ratchet - only tighten.
OK_SHARE_FLOOR = 0.90
TOTAL_DEV_CEILING = 0.10

pytestmark = pytest.mark.skipif(
    os.environ.get("OE_KCC_GOLDEN") != "1",
    reason="golden KCC harness is opt-in (heavy): set OE_KCC_GOLDEN=1",
)


def test_kcc9_golden_acceptance() -> None:
    assert (FIXTURES / "kcc9-test.xlsx").exists(), "kcc9 fixtures missing"
    proc = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "kcc_golden.py"), "--tag", "pytest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
        cwd=BACKEND,
    )
    out = proc.stdout + proc.stderr
    verdict = re.search(r"VERDICT ok=([\d.]+) tol=[\d.]+ rows=(\d+) .*total_dev=([+-]?[\d.]+)% (PASS|FAIL)", out)
    assert verdict, f"harness did not reach a verdict:\n{out[-3000:]}"
    ok_share, rows, total_dev, status = (
        float(verdict.group(1)),
        int(verdict.group(2)),
        float(verdict.group(3)) / 100.0,
        verdict.group(4),
    )
    assert rows >= 300, f"too few gold rows aligned ({rows}) - the comparison lost the bill"
    assert ok_share >= OK_SHARE_FLOOR, (
        f"within-10% share fell to {ok_share:.1%} (floor {OK_SHARE_FLOOR:.0%})"
    )
    assert abs(total_dev) <= TOTAL_DEV_CEILING, (
        f"bill total deviated {total_dev:+.1%} from gold (ceiling ±{TOTAL_DEV_CEILING:.0%})"
    )
    assert status == "PASS"
