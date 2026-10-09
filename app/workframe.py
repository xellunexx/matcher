"""Grounded work requirements; no LLM access, retrieval or price generation.

Quotes establish provenance, not the truth of an LLM's interpretation. Unknown,
inferred and incomplete frames can be reviewed but cannot authorise a price.
"""
from __future__ import annotations

import re
import math
import statistics
import unicodedata
from decimal import Decimal, InvalidOperation

from . import matcher

TRADES = frozenset("earthworks concrete masonry roofing sheet_metal carpentry tiling plastering flooring painting glazing steelwork waterproofing thermal_insulation doors_windows drywall plumbing sanitary_accessories hvac electrical low_current roads_paving landscaping demolition site equipment other unknown".split())
OPERATIONS = frozenset("new demolish reinstall demolish_reinstall replace rework haul test rent clean unknown".split())
SCOPES = frozenset("supply_install material labour machine lump unknown".split())
ROLES = frozenset("element accessory system resource unknown".split())
SPEC_KINDS = frozenset("thickness_mm diameter_mm size_cm concrete_class fraction_mm power_kw cross_section_mm2 height_m fire_rating other".split())
NORM_CHAPTERS = {
    "01": "Земни работи", "02": "Кофражни работи", "03": "Армировъчни работи",
    "04": "Бетонови работи", "05": "Зидарски работи", "06": "Покривни работи",
    "07": "Тенекеджийски работи", "08": "Дърводелски работи",
    "09": "Облицовъчни работи", "10": "Мазачески работи", "11": "Настилки",
    "12": "Стъкларски работи", "13": "Бояджийски работи", "14": "Железарски работи",
    "15": "Хидроизолации", "16": "Топлоизолации", "17": "Дограма и столарски работи",
    "18": "ОВК и отопление", "19": "Сухо строителство", "20": "ВиК инсталации в сгради",
    "21": "Външни ВиК мрежи и пътни възстановявания", "22": "Пътни работи и озеленяване",
    "23": "Укрепителни и хидротехнически", "24": "Електрически инсталации",
    "25": "Разрушителни и демонтажни работи", "00": "Друго/извън нормите",
}


def corpus_input(row):
    match = re.search(r"bl(\d\d)", row.get("category") or "", re.IGNORECASE) or re.match(r"(?:БЛ|СЕК)(\d\d)", row.get("code") or "")
    header = row.get("section") or (NORM_CHAPTERS.get(match.group(1), "") if match else "")
    return {"text": row.get("desc") or row.get("name") or "", "unit": row.get("unit") or "", "header": header}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _norm(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", _text(value)).casefold())


def _grounded(quote, src, *, header=False):
    quote = _norm(quote)
    if not quote:
        return False
    sources = [src.get("text")]
    if header:
        sources.append(src.get("header"))
    return any(re.search(r"(?<!\w)" + re.escape(quote) + r"(?!\w)", _norm(s))
               for s in sources)


def _key(value):
    return tuple(sorted(set(matcher.canonical_tokens(value))))


def _number(value):
    try:
        return format(Decimal(value.replace(",", ".")).normalize(), "f")
    except InvalidOperation:
        return None


def _scope_supported(value, quote):
    tokens = set(matcher.canonical_tokens(quote))
    s = _norm(quote)
    supply = "delivery" in tokens
    install = "installation" in tokens
    if value == "supply_install":
        return ((supply and install) or ("труд" in s and "материал" in s)) and not re.search(r"\b(?:без|само)\b", s)
    if value == "material":
        return supply and not install and not re.search(r"\bбез\b", s)
    if value == "labour":
        return bool(re.search(r"само\s+труд|без\s+(?:доставка|материал)|доставен|възложител", s))
    if value == "machine":
        return bool(re.search(r"машино|механиза", s))
    if value == "lump":
        return bool(re.search(r"паушал", s))
    return False


def _operation_supported(value, quote):
    tokens, text = set(matcher.canonical_tokens(quote)), _norm(quote)
    reverse = bool(re.search(r"обрат|повтор", text))
    return {
        "new": "installation" in tokens and not reverse,
        "demolish": "demolition" in tokens,
        "reinstall": "installation" in tokens and reverse,
        "demolish_reinstall": {"demolition", "installation"} <= tokens and reverse,
        "replace": bool(re.search(r"подмян|замян", text)),
        "rework": bool(re.search(r"преработ|ремонт|възстанов|корек", text)),
        "haul": "haulage" in tokens,
        "test": bool(re.search(r"изпит|измер|пуск|тест", text)),
        "rent": bool(re.search(r"наем", text)),
        "clean": bool(re.search(r"почист", text)),
    }.get(value, False)


def _technical_literals(text):
    number = r"\d+(?:[.,]\d+)?"
    pattern = (r"(?:\bc\s*\d+\s*/\s*\d+\b|(?:dn|ø|ф|d=)\s*\d+\b|"
               r"\b(?:rei|ei)\s*\d+\b|" + number +
               r"(?:\s*[x×х/\-]\s*" + number + r")*\s*"
               r"(?:мм2|mm2|мм|mm|см|cm|квт|kw|w|v|a|м|m)\b)")
    return re.findall(pattern, _norm(text))


def _spec_value(kind, quote):
    """Read the value from the cited source, not the model's numeric claim."""
    s = _norm(quote).replace(",", ".").replace("×", "x").replace("х", "x")
    num = r"\d+(?:\.\d+)?"
    if kind in ("thickness_mm", "diameter_mm", "height_m"):
        found = re.findall(r"(" + num + r")\s*(мм|mm|см|cm|м|m)\b", s)
        if len(found) != 1:
            if kind == "diameter_mm":
                found = re.findall(r"(?:ф|ø|dn|d=)\s*(" + num + r")\b", s)
                return _number(found[0]) if len(found) == 1 else None
            return None
        value, unit = found[0]
        factor = {"мм": "1", "mm": "1", "см": "10", "cm": "10", "м": "1000", "m": "1000"}[unit]
        value = Decimal(value) * Decimal(factor)
        if kind == "height_m":
            value /= 1000
        return format(value.normalize(), "f")
    if kind == "size_cm":
        found = re.findall(r"(" + num + r"(?:\s*[x/]\s*" + num + r"){1,2})\s*(мм|mm|см|cm|м|m)\b", s)
        if len(found) != 1:
            return None
        dims, unit = found[0]
        factor = {"мм": ".1", "mm": ".1", "см": "1", "cm": "1", "м": "100", "m": "100"}[unit]
        return "x".join(format((Decimal(n) * Decimal(factor)).normalize(), "f")
                        for n in re.split(r"\s*[x/]\s*", dims))
    patterns = {
        "concrete_class": r"\bc\s*(\d+\s*/\s*\d+)\b",
        "fraction_mm": r"\b(" + num + r"\s*[-/]\s*" + num + r")\s*(?:мм|mm)\b",
        "power_kw": r"\b(" + num + r")\s*(?:kw|квт)\b",
        "cross_section_mm2": r"\b(" + num + r"(?:\s*x\s*" + num + r")?)\s*(?:мм2|mm2)\b",
        "fire_rating": r"\b((?:rei|ei|re)\s*\d+)\b",
    }
    if kind == "other":
        return s
    found = re.findall(patterns.get(kind, r"(?!)"), s)
    if len(found) != 1:
        return None
    value = re.sub(r"\s+", "", found[0]).replace("/", "-" if kind == "fraction_mm" else "/")
    if kind == "concrete_class":
        return "c" + value
    if kind == "power_kw":
        return _number(value)
    return value


def validate(raw, src):
    """Fail closed on malformed/ungrounded slots and retain evidence per slot."""
    raw = raw if isinstance(raw, dict) else {}
    out = {k: _text(src.get(k)) for k in ("text", "unit", "header")}
    dropped, evidence = [], {}

    def slot(field, allowed, ev_key, header=False):
        value, quote = raw.get(field), _text(raw.get(ev_key))
        if not isinstance(value, str) or value not in allowed or value == "unknown":
            return "unknown"
        if not _grounded(quote, src, header=header):
            dropped.append(field)
            return "unknown"
        evidence[field] = quote
        return value

    out["trade"] = slot("trade", TRADES, "trade_ev", header=True)
    out["operation"] = slot("operation", OPERATIONS, "op_ev")
    if out["operation"] != "unknown" and not _operation_supported(out["operation"], evidence["operation"]):
        dropped.append("operation")
        out["operation"] = "unknown"
        evidence.pop("operation", None)
    out["object_role"] = slot("object_role", ROLES, "role_ev")
    out["scope"] = slot("scope", SCOPES, "scope_ev")
    if out["scope"] != "unknown" and not _scope_supported(out["scope"], evidence["scope"]):
        dropped.append("scope")
        out["scope"] = "unknown"
        evidence.pop("scope", None)
    basis = raw.get("scope_basis")
    out["scope_basis"] = "explicit" if out["scope"] != "unknown" and basis == "explicit" else "unknown"
    obj, quote = _text(raw.get("object")), _text(raw.get("object_ev"))
    out["object"] = obj.casefold() if obj and _grounded(quote, src) else ""
    if out["object"]:
        evidence["object"] = quote
    elif obj:
        dropped.append("object")

    def claims(field):
        result = []
        values = raw.get(field, [])
        if not isinstance(values, list):
            dropped.append(field)
            return result
        for item in values:
            if not isinstance(item, dict):
                dropped.append(field)
                continue
            value, ev = _text(item.get("value")), _text(item.get("ev"))
            if not value or not _grounded(ev, src) or _key(value) != _key(ev):
                dropped.append(field)
                continue
            result.append({"value": value.casefold(), "ev": ev})
        return result

    for field in ("material", "includes", "excludes"):
        out[field] = claims(field)
    operations = raw.get("operations", [])
    out["operations"] = []
    if not isinstance(operations, list):
        operations = []
        dropped.append("operations")
    for item in operations:
        if (isinstance(item, dict) and isinstance(item.get("value"), str)
                and item["value"] in OPERATIONS - {"unknown"}
                and _grounded(item.get("ev"), src)
                and _operation_supported(item["value"], item.get("ev"))):
            out["operations"].append({"value": item["value"], "ev": item["ev"]})
        else:
            dropped.append("operations")
    if out["operation"] != "unknown":
        out["operations"].append({"value": out["operation"], "ev": evidence["operation"]})
    covered = set().union(*(set(matcher.canonical_tokens(x["ev"])) for x in out["operations"]))
    declared = set(matcher.canonical_tokens(out["text"])) & {"installation", "demolition", "haulage"}
    if not declared <= covered:
        dropped.append("uncovered operation")
    out["specs"] = []
    specs = raw.get("specs", [])
    if not isinstance(specs, list):
        specs = []
        dropped.append("specs")
    for sp in specs:
        if not isinstance(sp, dict) or not isinstance(sp.get("kind"), str) or sp["kind"] not in SPEC_KINDS:
            dropped.append("spec")
            continue
        value = _spec_value(sp["kind"], sp.get("ev")) if _grounded(sp.get("ev"), src) else None
        claimed = _norm(str(sp.get("value", ""))).replace(",", ".").replace("×", "x").replace("х", "x")
        if value is None or claimed != value:
            dropped.append("spec")
            continue
        out["specs"].append({"kind": sp["kind"], "value": value, "ev": sp["ev"]})
    quotes = [re.sub(r"\s+", "", _norm(x["ev"])) for x in out["specs"]]
    if any(not any(re.sub(r"\s+", "", literal) in quote for quote in quotes)
           for literal in _technical_literals(out["text"])):
        dropped.append("uncovered technical specification")
    unit = matcher._unit_key(out["unit"])
    out["unit_dim"] = unit[0] if unit else "unknown"
    out["ambiguities"] = [_text(x) for x in raw.get("ambiguities", []) if _text(x)] if isinstance(raw.get("ambiguities", []), list) else ["malformed ambiguities"]
    out["evidence"], out["dropped"] = evidence, sorted(set(dropped))
    return out


def _claims(frame, field):
    return {_key(x["value"]) for x in frame.get(field, [])}


def compare(q, c):
    """Compatibility is conjunctive, not a score that can outweigh conflicts."""
    missing, conflicts = [], []
    for field in ("trade", "object_role", "unit_dim", "scope"):
        a, b = q.get(field, "unknown"), c.get(field, "unknown")
        if a in ("unknown", "other") or b in ("unknown", "other"):
            missing.append(field + " unknown")
        elif a != b:
            conflicts.append(field + " conflict")
    for frame in (q, c):
        if frame.get("scope_basis") != "explicit":
            missing.append("scope is not explicit")
        if frame.get("dropped"):
            missing.append("ungrounded or malformed claims")
        if frame.get("ambiguities"):
            missing.append("interpretation ambiguous")
    qo, co = _key(q.get("object", "")), _key(c.get("object", ""))
    if not qo or not co:
        missing.append("object unknown")
    elif qo != co:
        if set(qo) <= set(co) or set(co) <= set(qo):
            missing.append("object variant or accessory")
        else:
            conflicts.append("object conflict")
    elif _key(q.get("evidence", {}).get("object", "")) != _key(c.get("evidence", {}).get("object", "")):
        missing.append("object equivalence not corroborated by source")
    qops = {x["value"] for x in q.get("operations", [])}
    cops = {x["value"] for x in c.get("operations", [])}
    if not qops or not cops:
        missing.append("operation unknown")
    elif qops != cops:
        conflicts.append("operation bundle conflict")
    qm, cm = _claims(q, "material"), _claims(c, "material")
    if qm and cm and qm != cm:
        conflicts.append("material conflict")
    elif qm != cm:
        missing.append("material not fully evidenced")
    qs = {(x["kind"], x["value"]) for x in q.get("specs", [])}
    cs = {(x["kind"], x["value"]) for x in c.get("specs", [])}
    for kind in {x[0] for x in qs} & {x[0] for x in cs}:
        if {v for k, v in qs if k == kind} != {v for k, v in cs if k == kind}:
            conflicts.append("spec conflict: " + kind)
    if qs != cs:
        missing.append("specs not fully evidenced")
    if _claims(q, "includes") != _claims(c, "includes"):
        missing.append("included work differs or is missing")
    if _claims(q, "excludes") != _claims(c, "excludes"):
        missing.append("excluded work differs or is missing")
    if _claims(q, "includes") & _claims(c, "excludes") or _claims(c, "includes") & _claims(q, "excludes"):
        conflicts.append("included work explicitly excluded")
    if conflicts:
        return "reject", 0, sorted(set(conflicts + missing))
    if missing:
        return "review", 50, sorted(set(missing))
    return "match", 100, ["all declared work requirements agree"]


def select_price(query_frame, candidates, spread_limit=0.15):
    """Price only complete compatible work; expose every rejection and source."""
    audit, matched = [], []
    for row, frame in candidates:
        verdict, score, reasons = compare(query_frame, frame)
        if frame.get("text") != _text(row.get("desc") or row.get("name")) or frame.get("unit") != _text(row.get("unit")):
            verdict, reasons = "reject", reasons + ["frame does not belong to corpus row"]
        try:
            price = float(row.get("amount_eur") or 0)
        except (ValueError, TypeError):
            price = 0
        factor = matcher.unit_rate_factor(frame.get("unit"), query_frame.get("unit"))
        if row.get("status") != "active":
            verdict, reasons = "review", reasons + ["source not active"]
        if not row.get("id"):
            verdict, reasons = "reject", reasons + ["source id missing"]
        if not math.isfinite(price) or price <= 0 or factor is None:
            verdict, reasons = "reject", reasons + ["invalid price or unit conversion"]
        converted = price * float(factor) if factor is not None else None
        if converted is not None and (not math.isfinite(converted) or converted <= 0):
            verdict, reasons = "reject", reasons + ["invalid converted price"]
        entry = {"id": row.get("id"), "desc": row.get("desc"), "origin_ref": row.get("origin_ref"),
                 "source_key": row.get("source_key"), "verdict": verdict, "score": score,
                 "reasons": reasons, "unit_eur": converted, "frame": frame}
        audit.append(entry)
        if verdict == "match":
            matched.append(entry)
    result = {"verdict": "review" if audit else "none", "price": None,
              "price_kind": None, "evidence_ids": [], "query_frame": query_frame,
              "candidates": audit, "reason": "no complete compatible work evidence"}
    if not matched:
        return result
    prices = [x["unit_eur"] for x in matched]
    spread = (max(prices) - min(prices)) / min(prices)
    result["spread"] = spread
    if spread > spread_limit:
        result["reason"] = "compatible work has conflicting prices"
        return result
    result.update(verdict="match", price=statistics.median(prices),
                  price_kind="observed" if len(matched) == 1 else "derived_median",
                  evidence_ids=[x["id"] for x in matched], reason="compatible work and price agreement")
    return result
