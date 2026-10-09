# ── BOQ row classification ─────────────────────────────────────────────────────
# building identity markers in BOQ text: "Къща А", "Сграда 2", "House B", "bldg-2"
_MARK_WORD = r"(?i:къща|сграда|корпус|блок|обект|house|building|bldg|block)"
_CYR_ORD = "АБВГДЕЖЗИК"
_LAT_ORD = "ABCDEFGHIJ"


def _marker_regex(ordinal):
    """Regex matching BOQ text that names building `ordinal` (1-based): the word
    + the Cyrillic/Latin letter of that rank (uppercase; lowercase only when it
    closes the phrase, because "сграда в града" is a preposition), the plain
    number, or the scene id bldg-N."""
    n = int(ordinal)
    alts = [_MARK_WORD + r"\s*[-–]?\s*(?:№\s*)?" + str(n) + r"(?![\w\u0400-\u04ff])",
            r"bldg-" + str(n) + r"\b"]
    if 1 <= n <= len(_CYR_ORD):
        up = _CYR_ORD[n - 1] + _LAT_ORD[n - 1]
        alts.append(_MARK_WORD + r"\s*[-–]?\s*[" + up + r"](?![\w\u0400-\u04ff])(?!\s+[А-ЯA-Z]{2,})")
        alts.append(_MARK_WORD + r"\s*[-–]?\s*[" + up.lower() + r"]\s*(?:$|[-–:;,.)])")
    return re.compile("|".join(alts))


# category vocabularies, FIRST hit wins (ordered so that a compound description
# lands on its most specific object: "Изкоп за фундаменти" -> trench, not foundation)
_CAT_RX = {
    "fence": r"оград|плет|fence",
    "road": r"але[яи]|\bпът|асфалт|\broad|driveway|тротоар|паваж|бордюр|павета",
    "openings": r"дограма|прозор|\bврат|window|door|joinery",
    "roof": r"покрив|roof|керемид",
    "repair": r"ремонт|подмян|саниран|renovation|replacement",
    "trench": r"изкоп|насип|засипк|земни|excavat|earthwork",
    "foundation": r"фундамент|\bоснови\b|foundation|footing",
    "slab": r"плоча|плочи\b|slab",
    "wall": r"\bстен|зидари|зидан|тухл|преград|wall|masonry|brick",
    "insulate": r"топлоизол|(?<!хидро)изолац|insulat|минерална вата",
    "paint": r"боядисв|\bбоя\b|paint",
    "facade": r"фасад|мазилк|мозайч|facade",
    "finish": r"довърш|шпаклов|гипсокартон|облицов|ламинат|паркет|настилк|плочк|теракот|"
              r"гранитогрес|finish",
    "water": r"водопровод|водоснабд|\bвода\b|water|\bСВО\b",
    "sewer": r"канализац|\bканал|sewer|\bСКО\b",
    "mep": r"електро|\bвик\b|овк|hvac|инстал|mep|лифт|ventilat|отопл|климат|осветл|кабел|тръб|санитар",
    "site": r"озелен|благоустр|вертикална планировка|site|терен",
    "structure": r"конструкц|бетон|армат|кофраж|колон|гред|стоманобетон|beam|column|concrete|formwork|rebar",
}
_CAT_RX = {k: re.compile(v, re.I) for k, v in _CAT_RX.items()}
_NEW_ORDER = ("fence", "road", "openings", "roof", "trench", "foundation", "slab", "wall",
              "insulate", "facade", "finish", "paint", "water", "sewer", "mep", "site", "structure")
_RENO_ORDER = ("openings", "roof", "repair", "insulate", "paint", "facade", "wall", "mep",
               "water", "sewer", "finish", "fence", "road", "site", "slab", "structure")
# categories that are building fabric (a shared row splits across buildings);
# everything else is plot-level and lands on site objects
_FABRIC_CATS = frozenset(("openings", "roof", "repair", "trench", "foundation", "slab", "wall",
                          "insulate", "paint", "facade", "finish", "mep", "structure"))


def _row_text(row, *keys):
    return " ".join(str(row.get(k) or "") for k in keys)


def _classify_rows(rows, bids_tags, reno):
    """Rows -> [{key, scope, cat, qty, sum}] with scope in bids | 'shared'.
    Scope: building named in section/sub, else in desc; a row naming several
    buildings is shared. Unmarked rows go to the primary building when NO row in
    the pack names any building (single-building tenders), else to 'shared'."""
    markers = [(bid, _marker_regex(tag)) for bid, tag in bids_tags if tag]
    order = _RENO_ORDER if reno else _NEW_ORDER
    primary = bids_tags[0][0] if bids_tags else None
    out, any_marked = [], False
    for i, row in enumerate(rows):
        scope = None
        for text in (_row_text(row, "section", "sub"), _row_text(row, "desc")):
            hits = [bid for bid, rx in markers if rx.search(text)]
            if hits:
                scope = hits[0] if len(hits) == 1 else "shared"
                any_marked = True
                break
        text = _row_text(row, "section", "sub", "desc")
        cat = next((c for c in order if _CAT_RX[c].search(text)), "other")
        k = row.get("key")
        qty = row.get("qty")
        s = row.get("sumEur")
        out.append({"key": str(k) if k is not None else str(i), "scope": scope, "cat": cat,
                    "qty": float(qty) if isinstance(qty, (int, float)) else None,
                    "sum": float(s) if isinstance(s, (int, float)) else None})
    for r in out:
        if r["scope"] is None:
            # plot-level categories are never a building's own scope
            r["scope"] = "shared" if (any_marked or primary is None
                                      or r["cat"] not in _FABRIC_CATS) else primary
    return out


def _has_rows(crows, scope, cat, shared=True):
    ok = (scope, "shared") if shared else (scope,)
    return any(r["cat"] == cat and r["scope"] in ok for r in crows)

