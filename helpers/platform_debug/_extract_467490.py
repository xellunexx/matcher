# -*- coding: utf-8 -*-
"""467490 Radomir: the spec PDF's own tables -> draft КСС scope (deterministic).

pymupdf find_tables on 2.Техническа спецификация.pdf: rows are [no, desc, unit, qty];
section heads are rows with an empty qty. Every scope row keeps an evidence pin
(section + row no + PDF page). No number is invented — every qty comes from the doc.
Self-check prints per-section counts against the expected map."""
import json, re, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pymupdf

ROOT = Path(__file__).resolve().parent
SPEC_PDF = ROOT / "data/demo/processed/files/467490/58256222_2.Техническа спецификация.pdf"
OUT = ROOT / "data/demo/processed/files/467490/_kss_scope.json"

EXPECTED = {
    "ПЪРВОСТЕПЕННА АЛЕЯ - ш. 4м": 12, "ПЪРВОСТЕПЕННА АЛЕЯ - ш. 3.5м": 12,
    "ВТОРОСТЕПЕННА АЛЕЯ - ш. 3м": 11, "ВТОРОСТЕПЕННА АЛЕЯ - ш.2м": 12,
    "АЛЕЯ ЗА СПОРТ": 13, "СЦЕНА": 13, "ЧЕШМА": 1, "РЕЧНИ КОРИТА": 3,
    "ДЕТСКА ПЛОЩАДКА - 1": 25, "ДЕТСКА ПЛОЩАДКА - 2": 24, "ДЕТСКА ПЛОЩАДКА -3": 22,
    "ЛОСТОВЕ": 21, "ИГРИЩЕ": 14, "КУЧЕШКА": 6, "ПАРКОВО ОБЗАВЕЖДАНЕ": 4,
    "ОЗЕЛЕНЯВАНЕ": 2, "ЧАСТ ВИК": 21, "ЧАСТ ЕЛЕКТРО": 43,
}

_WORK_WORDS = ("изкоп", "доставка", "полагане", "товарене", "трамбоване", "валиране", "монтаж",
               "изграждане", "направа", "почистване", "изтегляне", "изправяне", "измерване",
               "двукратно", "контрол", "трасиране", "подготовка", "прехвърляне", "натоварване",
               "превоз", "уплатняване", "дезинфекция", "изпитване", "разчертаване", "основа")


def is_section_title(desc):
    """Section heads in this spec are uppercase-leaning. Work-row continuations split by page
    breaks start lowercase or with a work verb — those are text, never sections. Verbs in
    ALL-CAPS (ИЗГРАЖДАНЕ НА ЧЕШМА) are section heads, not work rows.
    Mixed-case-but-caps-led heads („ДЕТСКА ПЛОЩАДКА -3, деца със специални нужди") pass via
    the first-two-words-caps rule."""
    d = desc.strip()
    if len(d) < 4 or d[0].islower():
        return False
    letters = [c for c in d if c.isalpha()]
    caps = sum(c.isupper() for c in letters)
    if letters and caps / len(letters) >= 0.45:
        return True
    words = d.split()
    if len(words) >= 2 and all(len(w) >= 3 and w.upper() == w for w in words[:2]):
        return True
    if d.lower().startswith(_WORK_WORDS):
        return False
    return False


def bgnum(s):
    t = (s or "").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


def short_section(t):
    """Compact canonical section label."""
    t = clean(t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"/\d+\s*m2?/", "", t).strip(" —-/")
    return t[:60]


def extract():
    doc = pymupdf.open(str(SPEC_PDF))
    rows, section, sec_page = [], "ОБЩИ", 0
    for pno in range(len(doc)):
        tabs = doc[pno].find_tables()
        for tab in tabs.tables:
            for r in tab.extract():
                cells = [clean(c) for c in r]
                if len(cells) < 4:
                    continue
                # generic role placement: qty = last numeric-positive cell, unit = first unit-token,
                # desc = longest textual cell, no = first short numeric (or blank).
                txt_cells = [c for c in cells if c and bgnum(c) is None and re.search(r"[А-Яа-яA-Za-z]", c)]
                num_cells = [c for c in cells if bgnum(c) is not None and bgnum(c) > 0]
                unit_cells = [c for c in cells if c.lower() in ("м3", "м2", "бр", "бр.", "лм", "м", "м.", "кг", "т")]
                desc = max(txt_cells, key=len) if txt_cells else ""
                unit = unit_cells[0].lower().rstrip(".") if unit_cells else ""
                q = bgnum(num_cells[-1]) if num_cells else None
                no = cells[0] if re.fullmatch(r"\d{1,3}", cells[0]) else ""
                if not desc or desc.lower() in ("наименование на работите", "видове работи", "мярка", "количество"):
                    continue
                if (not num_cells) and desc and (not unit):
                    if not bgnum(desc) and is_section_title(desc):
                        section, sec_page = short_section(desc), pno + 1
                    continue
                if q is None or q <= 0:
                    continue
                rows.append({"section": section, "no": no, "desc": desc, "unit": unit or "бр",
                             "qty": round(q, 3), "page": pno + 1,
                             "evidence": f"Тех.спецификация стр. {pno+1} · „{section}“ · поз. {no or '—'}"})
    return rows


def main():
    rows = extract()
    from collections import Counter
    secs = Counter(r["section"] for r in rows)
    print(f"rows: {len(rows)}")
    mismatch = 0
    for s, c in secs.items():
        exp = next((v for k, v in EXPECTED.items() if s.startswith(k)), None)
        flag = "" if exp == c else f"  (expected {exp})"
        if exp != c:
            mismatch += 1
        print(f"  {c:3} | {s}{flag}")
    bad = [r for r in rows if r["qty"] <= 0 or len(r["desc"]) < 8]
    print("suspicious:", len(bad), "| section-count mismatches:", mismatch)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("scope ->", OUT)


if __name__ == "__main__":
    main()
