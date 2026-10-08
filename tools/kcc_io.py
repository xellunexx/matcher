# -*- coding: utf-8 -*-
"""Minimal KCC reader for the eval: rows with description + unit + numeric quantity.
Column layout of the pair files: [№, description, unit, qty, unit price, ...].
The last text-only row above a position is kept as its section header."""
import json
import _paths


def read_kcc(path):
    path = str(path)
    if path.lower().endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
    elif path.lower().endswith(".xls"):
        import xlrd
        sh = xlrd.open_workbook(path).sheets()[0]
        grid = [sh.row_values(i) for i in range(sh.nrows)]
    else:
        raise ValueError("only .xls/.xlsx")
    rows, header = [], ""
    for i, r in enumerate(grid):
        r = (list(r) + [None] * 6)[:6]
        desc, unit, qty, price = r[1], r[2], r[3], r[4]
        text = desc.strip() if isinstance(desc, str) else ""
        u = str(unit).strip() if unit not in (None, "") else ""
        if text and u and len(u) <= 14 and isinstance(qty, (int, float)) and not isinstance(qty, bool):
            rows.append({"i": i, "text": text, "unit": u, "qty": qty, "header": header,
                         "price": price if isinstance(price, (int, float)) and price > 0 else None})
        elif text and not u and len(text) < 160:
            header = text
    return rows


def eval_lines():
    """Every blank-KCC line whose priced twin carries a price. `hide` = the priced file
    name, which is also the corpus `origin_ref` of that tender's rows (used to hide the twin)."""
    out = []
    for blank, priced in _paths.PAIRS:
        truth = {r["i"]: r["price"] for r in read_kcc(_paths.KCC_PAIRS / priced)}
        for r in read_kcc(_paths.KCC_PAIRS / blank):
            if truth.get(r["i"]):
                out.append({**r, "truth": truth[r["i"]], "hide": priced, "kcc": blank})
    return out


if __name__ == "__main__":
    lines = eval_lines()
    (_paths.WORK / "eval_lines.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
    print(len(lines), "eval lines ->", _paths.WORK / "eval_lines.json")
