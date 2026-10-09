# -*- coding: utf-8 -*-
"""test_acceptance_money.py — pin the acceptance probe's HUD money parser.
Pure, no server/browser. Run: py -3 test_acceptance_money.py

Formats under contract (implementnow §7D):
  "1 051 176,00" / "1,051,176.00" / "1051176.00" / "1051176,00"
plus UI edge cases (currency symbol, nbsp, blanks, negatives absent).
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts" / "acceptance"))
from spatial_acceptance_probe import money_num, pct_num

failures = []
def ok(cond, name, got=""):
    if cond:
        print(f"  ok - {name}")
    else:
        failures.append(name)
        print(f"  FAIL - {name} got={got!r}")

CASES = [
    ("1 051 176,00", 1051176.0),   # BG/EU, thin spaces, comma decimals
    ("1 051 176,00 €", 1051176.0),
    ("1 051 176,00 €", 1051176.0),  # nbsp + €
    ("1,051,176.00", 1051176.0),   # EN thousands + dot decimals
    ("1051176.00", 1051176.0),
    ("1051176,00", 1051176.0),
    ("0,00 €", 0.0),
    ("0.00", 0.0),
    ("27 267,86 €", 27267.86),
    ("33 чакат цена", None) ,       # text without money → None
    ("", None),
    (None, None),
    ("12,345", 12345),              # 3-digit group after comma → thousands
    ("336,5", 336.5),               # 1 digit after comma → decimal
]
for txt, want in CASES:
    got = money_num(txt)
    if want is None:
        ok(got is None, f"money_num({txt!r}) is None", got)
    else:
        ok(got is not None and abs(got - want) < 1e-9, f"money_num({txt!r}) == {want}", got)

ok(abs((pct_num("81%") or 0) - 0.81) < 1e-9, "pct_num('81%') == 0.81")
ok(pct_num("—") is None, "pct_num('—') is None")

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("acceptance money parser: all tests passed")
