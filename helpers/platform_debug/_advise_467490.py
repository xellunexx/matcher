# -*- coding: utf-8 -*-
"""467490 Radomir: supplier-catalog advisory for the human-gated draft rows.

Source: catalogs.leka.studio/vinci dump (window.IC_DATA, 2579 products: play/park/sport/flooring,
age envelopes, dims, fall heights). NO prices exist there -> canonical: product advisory only,
never a price. A blank row stays blank; it gains a "кандидат продукт" note for the human.

Mapping is deterministic: section family pins the catalog purpose; desc-token overlap picks
the product; spec-required age group / fall height filter first when the row says so.
"""
import json, re, sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent
CATJS = Path(r"C:\Users\ochak\.kimi-code\sessions\wd_tenderops_1d87d3840911\session_dedfa466-5e7a-42f6-9612-a26b65e34463\attachments\f_ecaf82b9-199f-4b84-bf24-a7f693cb747b-catalog.js")

raw = CATJS.read_text(encoding="utf-8")
m = re.search(r"window\.IC_DATA=(\{.*\})\s*;?\s*$", raw, re.S)
CAT = json.loads(m.group(1))["products"]
CAT = [{"id": p["id"], "n": p["n"], "cat": p.get("cat"), "purp": p.get("purp"),
        "brand": p.get("brand"), "series": p.get("series") or p.get("sname") or "",
        "dims": (p.get("sp") or {}).get("d") or "", "fall": (p.get("sp") or {}).get("f") or "",
        "age": p.get("age")} for p in CAT]

SECTION_PURP = {
    "ДЕТСКА ПЛОЩАДКА": ["play"],
    "КУЧЕШКА": ["play"],
    "ЛОСТОВЕ": ["sport"], "ИГРИЩЕ": ["sport"],
    "ПАРКОВО ОБЗАВЕЖДАНЕ": ["park"], "ОЗЕЛЕНЯВАНЕ": [],
    "АЛЕЯ": ["flooring"], "СЦЕНА": ["flooring"], "ЧЕШМА": [], "РЕЧНИ": [],
    "ЧАСТ ВИК": [], "ЧАСТ ЕЛЕКТРО": [],
}
STOP = {"на", "за", "от", "с", "по", "в", "до", "при", "и", "доставка", "монтаж", "вкл", "тип"}


def toks(s):
    return {t for t in re.findall(r"[а-яa-z]{3,}", (s or "").lower()) if t not in STOP}


def _teq(a, b):
    """Language-aware token equality for BG inflections (пейки/пейка, люлка/люлка…):
    identical, shared stem of the first min(len)-1..-esc chars, or first-4 for >=5-char words.
    Advisory tolerance by design (резултатът е кандидат, не решение)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) <= 1 and a[: min(la, lb) - 1] == b[: min(la, lb) - 1] and min(la, lb) >= 4:
        return True
    n = min(la, lb, 5)
    return n >= 4 and a[:n] == b[:n]


def age_need(desc):
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*г", desc)
    return (int(m.group(1)), int(m.group(2))) if m else None


def agefits(prod_age, need):
    if not prod_age:
        return True
    lo, hi = prod_age
    return lo <= need[0] and hi >= need[1]


def advise(row):
    sec = row["section"]
    purps = next((v for k, v in SECTION_PURP.items() if k in sec), [])
    if not purps:
        return None, 0
    qt = toks(row["desc"])
    if not qt:
        return None, 0
    need = age_need(row["desc"])
    best, best_s = None, 0.0
    for c in CAT:
        if c["cat"] not in purps:  # cat: play|sport|park|flooring; purp is the subtype
            continue
        if need and not agefits(c.get("age"), need):
            continue
        ct = toks(c["n"])
        inter_pairs = [(a, b) for a in qt for b in ct if _teq(a, b)]
        inter = len({a for a, _ in inter_pairs})
        if not inter:
            continue
        s = inter * 2.0 / (len(qt) + len(ct)) + (0.15 if c["brand"] == "vinci" else 0) \
            + (0.1 if c["series"] and c["series"].lower() in (c["n"] or "").lower() else 0)
        if s > best_s:
            best, best_s = c, s
    return (best, round(best_s, 3)) if best and best_s >= 0.5 else (None, round(best_s, 3))


def main():
    rows = json.loads((ROOT / "data/demo/processed/files/467490/_kss_scope.json").read_text(encoding="utf-8"))
    priced = json.loads((ROOT / "tenderexamples" / "_467490_priced_flags.json").read_text(encoding="utf-8")) \
        if (ROOT / "tenderexamples" / "_467490_priced_flags.json").exists() else None
    covered, stats = 0, Counter()
    out = []
    for r in rows:
        best, score = advise(r)
        if best:
            covered += 1
            stats[r["section"]] += 1
            out.append({**r, "product": {"name": best["n"], "brand": best["brand"],
                                         "series": best["series"], "dims": best["dims"],
                                         "fall": best["fall"], "id": best["id"], "score": score}})
        else:
            out.append({**r, "product": None})
    dst = ROOT / "data/demo/processed/files/467490/_kss_scope_advised.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"catalog: {len(CAT)} products | advised rows: {covered}/{len(rows)}")
    for s, c in stats.most_common():
        print(f"  {c:3} | {s}")
    print("->", dst)


if __name__ == "__main__":
    main()
