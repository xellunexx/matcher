"""Consensus pools generator (deterministic crawl — Stage: SEK norms pool).

Crawls analysis workbooks (анализни цени .xlsx, one sheet = one priced KCC line's
analysis) and groups every KCC line by the unique SEK norm code in its
`Шифър (код)` column. Output: one consensus pool per unique norm — every unique
KCC line hooked to the norm is a candidate; the pool median IS the SEK price.

Before pooling, each SEK norm gets its own price median — 2026-dated lines only
(the norm's price baseline). Lines with no usable date are extracted, counted as
`undated`, and excluded from the 2026 median — never silently dropped.

Dates come from the heap convention (Stage 1: zips per tender ID+date): pass
`--file-dates <csv|json>` mapping filename -> ISO date (or bare year). A file
with no mapping is `undated`.

Rules enforced (DB_SOURCES.md): every sheet extracted; a sheet that reads empty
is doubted and logged, never trusted; nothing skipped silently (drops logged).

Usage:
    py tools/build_consensus_pools.py <workbook_or_dir> [...] --out work/consensus_pools.json [--file-dates dates.csv] [--year 2026]
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
from pathlib import Path

import openpyxl

_HEADER_WORDS = ("Шифър", "Наименование", "Мярка")
_SKIP_LABELS = ("труд", "матери", "механизация", "общо", "доп.разходи", "доп разходи")


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("€", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
        s = s.rstrip(".")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _text(v):
    return " ".join(str(v).split()) if v is not None else ""


def _sek_key(code: str) -> tuple[str, bool]:
    """Normalize a Шифър cell -> (canonical key, starred flag)."""
    c = _text(code).upper().replace(" ", "")
    starred = c.startswith("*")
    return (c.lstrip("*").rstrip("#"), starred)


_KSO_RE = re.compile(r"^(КСО)?\d{2}\.\d{2,3}")


def _scheme(code: str) -> str:
    """Classify a norm code: 'sek' | 'kso' | 'other'. Never mix schemes in a pool."""
    c = code.upper()
    if c.startswith("СЕК"):
        return "sek"
    if _KSO_RE.match(c) or c.startswith("КСО"):
        return "kso"
    if re.fullmatch(r"\d{6,}", c):
        return "sek"
    return "other"


def _find_header(rows):
    for i, row in enumerate(rows):
        cells = [_text(c) for c in row]
        if sum(any(w.lower() in c.lower() for w in _HEADER_WORDS) for c in cells) >= 2:
            cols = {}
            for j, c in enumerate(cells):
                lc = c.lower()
                if "ном" in lc and "col_pos" not in cols:
                    cols["pos"] = j
                if "шифър" in lc and "code" not in cols:
                    cols["code"] = j
                if "наимен" in lc and "name" not in cols:
                    cols["name"] = j
                if "мярка" in lc and "unit" not in cols:
                    cols["unit"] = j
                if "ед.цена" in lc or "ед цена" in lc:
                    cols.setdefault("price", j)
                if lc.startswith("колич"):
                    cols.setdefault("qty", j)
            return i, cols
    return None, None


def _load_file_dates(arg: str | None) -> dict[str, int]:
    """filename -> year. Accepts .json ({file: date|year}) or .csv (file,date)."""
    if not arg:
        return {}
    p = Path(arg)
    if p.suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        items = raw.items() if isinstance(raw, dict) else raw
        return {str(k): int(str(v)[:4]) for k, v in items}
    out = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if "," in ln:
            k, _, v = ln.partition(",")
            m = __import__("re").search(r"20\d\d", v)
            if m:
                out[k.strip()] = int(m.group(0))
    return out


def extract_lines(path: Path, year: int | None = None):
    """Yield (sheet, line dict, raw cell count) per position row in every sheet."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        nonempty = sum(1 for r in rows if any(_text(c) for c in r))
        hi, cols = _find_header(rows)
        if hi is None or "code" not in cols or "name" not in cols:
            # Format B: free-form per-position analysis, no Шифър table.
            # Capture name + Общо total as an UNHOOKED line — never silent.
            name = _text(rows[0][0]) if rows else ""
            total = None
            for r in rows:
                label = _text(r[0]).lower() if r else ""
                if label.startswith("общо") or label.startswith("всичко"):
                    for c in r[1:]:
                        n = _num(c)
                        if n:
                            total = n
            yield ws.title, {
                "pos": None, "code": "", "starred": False,
                "text": name, "unit": "", "qty": None,
                "unit_price_eur": None, "total_eur": total,
                "file": path.name, "sheet": ws.title, "year": year,
            }, nonempty
            continue
        current = None
        for row in rows[hi + 1 :]:
            cells = list(row)
            def cell(k):
                j = cols.get(k)
                return cells[j] if j is not None and j < len(cells) else None
            posval = cell("pos")  # noqa: E731
            is_pos = _num(posval) is not None and float(_num(posval)).is_integer()
            label = _text(cell("name")).lower()
            if is_pos and not any(label.startswith(s) for s in _SKIP_LABELS):
                if current:
                    yield ws.title, current, nonempty
                code, starred = _sek_key(cell("code"))
                current = {
                    "pos": int(_num(posval)),
                    "code": code,
                    "starred": starred,
                    "text": _text(cell("name")),
                    "unit": _text(cell("unit")),
                    "qty": _num(cell("qty")),
                    "unit_price_eur": _num(cell("price")),
                    "file": path.name,
                    "sheet": ws.title,
                    "year": year,
                }
            elif current and _text(cell("name")) and not is_pos:
                if not any(label.startswith(s) for s in _SKIP_LABELS) and not current["unit"]:
                    # wrapped continuation of the position's wording
                    current["text"] += " " + _text(cell("name"))
        if current:
            yield ws.title, current, nonempty
    wb.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path("work/consensus_pools.json")
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
    file_dates = _load_file_dates(
        sys.argv[sys.argv.index("--file-dates") + 1] if "--file-dates" in sys.argv else None
    )
    target_year = int(sys.argv[sys.argv.index("--year") + 1]) if "--year" in sys.argv else 2026
    inputs: list[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            inputs += sorted(p.rglob("*.xlsx"))
        elif p.suffix.lower() == ".xlsx":
            inputs.append(p)
    if not inputs:
        print("no .xlsx inputs found")
        sys.exit(1)

    pools: dict[str, dict] = {}
    drops = []
    unhooked: list[dict] = []
    empty_sheets = []
    seen = set()
    dups = 0
    for f in inputs:
        yr = file_dates.get(f.name)
        for sheet, line, nonempty in extract_lines(f, yr):
            if line is None:
                empty_sheets.append(f"{f.name}::{sheet} ({nonempty} nonempty cells)")
                continue
            if not line["code"]:
                unhooked.append(line)  # no Шифър — analysis w/o norm counterpart
                continue
            dedup_key = (line["code"], line["text"], line["unit_price_eur"], line["file"])
            if dedup_key in seen:
                dups += 1
                continue
            seen.add(dedup_key)
            scheme = _scheme(line["code"])
            pool = pools.setdefault(
                f"{scheme}:{line['code']}",
                {"scheme": scheme, "sek_code": line["code"], "unit": line["unit"], "lines": []},
            )
            pool["lines"].append(line)

    for code, pool in pools.items():
        prices = [l["unit_price_eur"] for l in pool["lines"] if l["unit_price_eur"]]
        y_prices = [
            l["unit_price_eur"] for l in pool["lines"]
            if l["unit_price_eur"] and l.get("year") == target_year
        ]
        pool["count"] = len(pool["lines"])
        pool["median_eur"] = statistics.median(prices) if prices else None
        pool[f"median_eur_{target_year}"] = statistics.median(y_prices) if y_prices else None
        pool["lines_dated"] = sum(1 for l in pool["lines"] if l.get("year") == target_year)
        pool["lines_undated"] = sum(1 for l in pool["lines"] if not l.get("year"))
        pool["starred"] = any(l["starred"] for l in pool["lines"])
        pool["lines"].sort(key=lambda l: (l["file"], l["sheet"], l["pos"] or 0))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump({"pools": pools, "unhooked_analyses": unhooked}, fh, ensure_ascii=False, indent=1)
    print(f"files: {len(inputs)} | pools: {len(pools)} | hooked lines: {sum(p['count'] for p in pools.values())} | dup lines skipped: {dups}")
    per_scheme = {}
    for p in pools.values():
        per_scheme[p["scheme"]] = per_scheme.get(p["scheme"], 0) + 1
    print("pools per scheme:", per_scheme)
    print(f"dated({target_year}) lines: {sum(p['lines_dated'] for p in pools.values())} | undated lines: {sum(p['lines_undated'] for p in pools.values())}")
    print(f"unhooked analyses (no Шифър): {len(unhooked)} | empty/headerless sheets (doubted): {len(empty_sheets)}")
    for s in empty_sheets[:10]:
        print("  EMPTY?", s)
    for d in drops[:10]:
        print("  DROP", d)
    top = sorted(pools.values(), key=lambda p: -p["count"])[:5]
    for p in top:
        print(f"  top pool {p['scheme']}:{p['sek_code']}: {p['count']} lines, median {p['median_eur']}, unit {p['unit']}")
    uprices = sum(1 for p in pools.values() for l in p["lines"] if l["unit_price_eur"])
    print(f"hooked lines with unit price: {uprices}")


if __name__ == "__main__":
    main()
