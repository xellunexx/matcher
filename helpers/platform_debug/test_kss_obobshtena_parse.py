# -*- coding: utf-8 -*-
"""Regression: обобщена КС sheet parsing (defect found on Hisarya КСС, 2026-10-03).

Two bugs in _price_roles / parse_kss_priced:
  A) the document TITLE row „ОБОБЩЕНА КОЛИЧЕСТВЕНА СМЕТКА" was accepted as a column
     header (substring „количеств" -> {qty: 0}), so every subsequent row read
     qty from column 0 = the item №, and unit defaulted to „бр".
  B) the real header „количество общо" matched the total branch („общо") before
     qty, so the quantity column was never bound.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("pipeline", str(ROOT / "app" / "pipeline.py"))
pipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipe)

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


# --- unit-level: _price_roles contract ---
check("title row is not a header",
      pipe._price_roles(["ОБОБЩЕНА КОЛИЧЕСТВЕНА СМЕТКА", "", "", ""]) is None,
      str(pipe._price_roles(["ОБОБЩЕНА КОЛИЧЕСТВЕНА СМЕТКА", "", "", ""])))
hdr = pipe._price_roles(["№", "ВИДОВЕ  СМР", "един. мярка", "количество общо"])
check("summary header maps qty->3", bool(hdr) and hdr.get("qty") == 3, str(hdr))
check("summary header maps unit->2", bool(hdr) and hdr.get("unit") == 2, str(hdr))
check("summary header maps no->0", bool(hdr) and hdr.get("no") == 0, str(hdr))

# --- end-to-end: synthetic .xlsx mirroring the Hisarya summary-sheet layout ---
import openpyxl  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="kss_hdr_"))
xlsx = tmp / "kss_summary.xlsx"
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Обобщена КС"
ws.append(["ОБЕКТ: тестов обект"])
ws.append([])
ws.append(["ОБОБЩЕНА КОЛИЧЕСТВЕНА СМЕТКА"])
ws.append([])
ws.append(["№", "ВИДОВЕ  СМР", "един. мярка", "количество общо"])
ws.append(["Част: Разрушаване на съществуващи постройки"])
ws.append([1, "Разрушаване на сгради и басейни с багер-чук", "м3", 962.8])
ws.append([2, "Топлоизолация от XPS с d = 5см по таван", "м2", 642.1531])
ws.append([])
ws.append(["№", "СМР", "Ед.м.", "  Кол-во"])   # later-section variant: „Ед.м.“ unit label
ws.append([9, "Измазване на улеите във вътрешен басейн", "м", 25.0])
wb.save(xlsx)

rows = pipe.parse_kss_priced(xlsx)
r1 = next((r for r in rows if "багер-чук" in r["desc"]), None)
check("qty = real quantity (962.8), not item №", bool(r1) and abs(r1["qty"] - 962.8) < 1e-6, str(r1))
check("unit = м3 from doc, not default бр", bool(r1) and r1["unit"] == "м3", str(r1))
r2 = next((r for r in rows if "XPS" in r["desc"]), None)
check("row2 qty=642.1531 unit=м2", bool(r2) and abs(r2["qty"] - 642.1531) < 1e-6 and r2["unit"] == "м2",
      str(r2))
r3 = next((r for r in rows if "Измазване на улеите" in r["desc"]), None)
check("variant header „Ед.м.“ keeps unit=м", bool(r3) and r3["unit"] == "м" and abs(r3["qty"] - 25.0) < 1e-6,
      str(r3))
check("no own_price invented (unpriced doc)", all("own_price" not in r for r in rows), "")
check("row count = 3", len(rows) == 3, str(len(rows)))

print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
