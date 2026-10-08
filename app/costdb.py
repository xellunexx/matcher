# -*- coding: utf-8 -*-
"""
TenderOps canonical local cost database.

JSON remains the import/export boundary. SQLite is the canonical runtime store.
- stdlib sqlite3 only
- FTS5 candidate retrieval
- WAL for concurrent local server requests
- source registry + normalized cost items
- importer is idempotent by source-file SHA-256
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from . import telemetry
except Exception:
    import importlib.util as _ilu
    def _telemetry_mod():
        p = Path(__file__).resolve().parent / "telemetry.py"
        if p.exists():
            spec = _ilu.spec_from_file_location("telemetry", str(p))
            m = _ilu.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
        return None
    telemetry = _telemetry_mod()


def _sibling(name):
    import importlib.util, sys
    p = Path(__file__).resolve().parent / f"{name}.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, str(p))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# Evidence matcher + curated Bulgarian bill-term aliases, ported from the ERP
# cost-match engine. Both degrade gracefully: retrieval falls back to the
# legacy token set when the modules are absent.
matcher = _sibling("matcher")
corpus_aliases = _sibling("corpus_aliases")

EURBGN = 1.95583
DB_VERSION = 7  # v7: FTS reindex — concept surfaces + bill_terms + casefold
DB_ENV = "TENDEROPS_DB_PATH"

SCHEMA_SQL = r"""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_sources (
    source_key TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    file_name TEXT,
    file_sha256 TEXT,
    as_of TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cost_items (
    id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL REFERENCES cost_sources(source_key),
    name TEXT NOT NULL,
    desc TEXT NOT NULL,
    category TEXT,
    section TEXT,
    unit TEXT NOT NULL,
    amount_eur REAL NOT NULL CHECK(amount_eur > 0),
    original_amount REAL,
    currency TEXT NOT NULL,
    vat_included INTEGER NOT NULL CHECK(vat_included IN (0,1)),
    as_of TEXT NOT NULL,
    derived_from_bgn REAL,
    origin_kind TEXT NOT NULL,
    origin_ref TEXT,
    origin_note TEXT,
    validity_from TEXT,
    validity_to TEXT,
    review_by TEXT,
    status TEXT NOT NULL CHECK(status IN ('active','pending_review','retired')),
    region TEXT,
    code TEXT,
    extra_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_items_active_unit ON cost_items(status, unit);
CREATE INDEX IF NOT EXISTS idx_cost_items_source ON cost_items(source_key);
CREATE INDEX IF NOT EXISTS idx_cost_items_code ON cost_items(code);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tender_id INTEGER, boq_key TEXT,
  boq_desc TEXT, boq_unit TEXT, boq_qty REAL,
  chosen_item_id TEXT, method TEXT, score REAL, margin REAL, confidence TEXT,
  evidence_json TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS match_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, match_id INTEGER REFERENCES matches(id),
  item_id TEXT, rank INTEGER, score REAL, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT, action TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tender_id INTEGER, started_at TEXT, finished_at TEXT,
  state TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER REFERENCES runs(id), at TEXT, state TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tender_id INTEGER, bid_amount REAL, submitted_at TEXT,
  won INTEGER, awarded_amount REAL, actual_cost REAL, margin REAL, completion_date TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS tender_fingerprints (
  tender_id INTEGER PRIMARY KEY, tokens_json TEXT, rows INTEGER, priced INTEGER, total_eur REAL, at TEXT
);
-- Gate 1 #2: immutable cost-environment versions. Rows are INSERT-only by contract.
CREATE TABLE IF NOT EXISTS cost_env_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  sources_json TEXT NOT NULL, note TEXT
);
-- Gate 1 #3: human resolutions live HERE (JSON file = export boundary only).
CREATE TABLE IF NOT EXISTS human_resolutions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tender_id INTEGER, boq_key TEXT NOT NULL,
  note TEXT, actor TEXT, at TEXT, rationale TEXT, evidence_json TEXT,
  UNIQUE(tender_id, boq_key)
);
CREATE VIRTUAL TABLE IF NOT EXISTS cost_items_fts USING fts5(
    id UNINDEXED,
    search_text,
    tokenize='unicode61 remove_diacritics 0'
);
CREATE TRIGGER IF NOT EXISTS cost_items_ai AFTER INSERT ON cost_items BEGIN
  INSERT INTO cost_items_fts(id,search_text)
  VALUES (new.id, norm_search_text(new.name, new.desc, COALESCE(new.category,''), COALESCE(new.section,'')));
END;
CREATE TRIGGER IF NOT EXISTS cost_items_ad AFTER DELETE ON cost_items BEGIN
  DELETE FROM cost_items_fts WHERE id = old.id;
END;
CREATE TRIGGER IF NOT EXISTS cost_items_au AFTER UPDATE ON cost_items BEGIN
  DELETE FROM cost_items_fts WHERE id = old.id;
  INSERT INTO cost_items_fts(id,search_text)
  VALUES (new.id, norm_search_text(new.name, new.desc, COALESCE(new.category,''), COALESCE(new.section,'')));
END;
"""

SOURCE_META = {
    "costdb_seed_2026.json": {
        "source_key": "seed_2026",
        "source_type": "local_seed",
        "display_name": "TenderOps 2026 local cost seed",
        "as_of": "2026",
    },
    "costdb_seed_webanchors.json": {
        "source_key": "webanchors",
        "source_type": "reference_web",
        "display_name": "Web price anchors harvest (advisory, pending_review)",
        "as_of": "2026-09",
    },
    "costdb_seed_buildly_smr.json": {
        "source_key": "buildly_smr",
        "source_type": "reference_web",
        "display_name": "Buildly СМР позиции — юли 2026 (скреп • pending_review)",
        "as_of": "2026-07",
    },
    "costdb_seed_buildly_materials.json": {
        "source_key": "buildly_materials",
        "source_type": "reference_web",
        "display_name": "Buildly материали — юли 2026 (скреп • pending_review)",
        "as_of": "2026-07",
    },
    "costdb_seed_buildly_machinery.json": {
        "source_key": "buildly_machinery",
        "source_type": "reference_web",
        "display_name": "Buildly наем механизация — юли 2026 (скреп • pending_review)",
        "as_of": "2026-07",
    },
    "costdb_seed_buildly_labor.json": {
        "source_key": "buildly_labor",
        "source_type": "reference_web",
        "display_name": "Buildly труд — юли 2026 (скреп • pending_review)",
        "as_of": "2026-07",
    },
    "costdb_seed_sek_2026.json": {
        "source_key": "sek_2026",
        "source_type": "sek_reference",
        "display_name": "SEK Online Construction Price Reference — July 2026",
        "as_of": "2026-07",
    },
    "costdb_seed.json": {
        "source_key": "legacy_seed",
        "source_type": "legacy_seed",
        "display_name": "TenderOps legacy cost seed",
        "as_of": "legacy",
    },
}

ORIGIN_PRIORITY = {
    "company_actual": 100,
    "won_tender": 92,
    "current_year_kss": 88,
    "supplier_quote": 84,
    "imported_workbook": 82,
    "supplier_web_anchor": 55,
    "estimate_book": 30,
}

UNIT_ALIASES_DEFAULT = {
    "бр": "PIECE", "бр.": "PIECE", "брой": "PIECE", "pc": "PIECE", "pcs": "PIECE",
    "м": "M", "м.": "M", "m": "M", "м1": "M", "л.м.": "M", "линеенметър": "M",
    "м2": "M2", "м²": "M2", "кв.м": "M2", "кв. м": "M2", "m2": "M2", "m²": "M2",
    "м3": "M3", "м³": "M3", "куб.м": "M3", "куб. м": "M3", "m3": "M3",
    "кг": "KG", "кг.": "KG", "kg": "KG", "т": "T", "тон": "T", "тона": "T", "t": "T",
    "ч": "HOUR", "ч.": "HOUR", "час": "HOUR", "часове": "HOUR",
    "ден": "DAY", "дни": "DAY", "денонощие": "DAY",
    "глобалнасума": "LSUM", "глоб.сума": "LSUM", "паушал": "LSUM", "паушалнасумa": "LSUM"
}

_UNIT_ALIASES_NORM = None  # dotless/spaceless alias map, built lazily by _norm_unit

LAT2CYR = str.maketrans({
    "A":"А","B":"В","C":"С","E":"Е","H":"Н","K":"К","M":"М","O":"О","P":"Р","T":"Т","X":"Х","Y":"У",
    "a":"а","c":"с","e":"е","h":"н","i":"и","k":"к","m":"м","o":"о","p":"р","t":"т","x":"х","y":"у"
})

STOP = {
    "на","за","от","с","по","в","до","при","и","или","като","вкл","включително","доставка",
    "монтаж","изпълнение","материали","работа","без","със","над","под","вид","нов","нова","нови","ново",
    "съществуваща","съществуващ","съществуващи","за","при"
}

# Controlled equivalence families. Keep these conservative: they are used only for candidate ranking.
SYNONYMS = {
    "бетонов": "бетон", "бетонова": "бетон", "бетонови": "бетон", "бетониране": "бетон",
    "арматура": "арматура", "армировка": "арматура", "армиране": "арматура",
    "зидане": "зидария", "зидарски": "зидария", "зидар": "зидария",
    "кофражен": "кофраж", "кофражни": "кофраж",
    "мазилки": "мазилка", "мазач": "мазилка", "шпакловане": "шпакловка", "шпакловки": "шпакловка",
    "боядисване": "боя", "боядисан": "боя", "бояджийски": "боя",
    "изолации": "изолация", "изолиране": "изолация",
    "покривни": "покрив", "покривна": "покрив", "покривно": "покрив",
    "керемиди": "керемида", "керемиден": "керемида",
    "тръби": "тръба", "тръбен": "тръба", "тръбопровод": "тръба",
    "кабели": "кабел", "кабелен": "кабел",
    "канализационен": "канализация", "канализационни": "канализация",
    "водопроводен": "водопровод", "водопроводни": "водопровод",
    "гипсокартонени": "гипсокартон", "гипсокартонна": "гипсокартон",
    "полагане": "полагане", "поставяне": "монтаж",
    "доставяне": "доставка", "доставя": "доставка",
    "отсичане": "изсичане", "изрязване": "изсичане", "дървета": "дърво", "дървесина": "дърво",
    "настилки": "настилка",
    # Operation verbs — one canonical token per work-op family. Without these, KCC
    # „разрушаване" never retrieves catalogue „разваляне/разбиване/демонтаж" (zero
    # shared tokens) while „бетон/плочи" tokens pull in opposite-work candidates.
    "разваляне": "разрушаване", "развален": "разрушаване", "развалена": "разрушаване",
    "развалени": "разрушаване", "разрушена": "разрушаване", "разрушен": "разрушаване",
    "разрушени": "разрушаване", "разбиване": "разрушаване", "разбит": "разрушаване",
    "разбита": "разрушаване", "демонтаж": "разрушаване", "демонтиране": "разрушаване",
    "демонтиран": "разрушаване", "къртене": "разрушаване", "кърт": "разрушаване",
    "кърти": "разрушаване", "къртен": "разрушаване", "очукване": "разрушаване",
    "очукане": "разрушаване", "очукан": "разрушаване", "изкърпване": "разрушаване",
    "демолиране": "разрушаване", "разрушавене": "разрушаване",
    "изкопаване": "изкоп", "изкопа": "изкоп", "изкопи": "изкоп",
    "насипване": "насип", "засипване": "насип",
    # Material inflection/synonym families that neither shared stems nor the
    # suffix stripper can bridge (БЛ15.027 never surfaced for „полистирол във
    # фугата": полистирол vs пенополистирен, фугата vs фуги).
    "полистирол": "полистирен", "пенополистирен": "полистирен",
    "пенополистирол": "полистирен", "стиропор": "полистирен",
    "фуги": "фуга", "фугата": "фуга", "фугиране": "фуга", "фугираща": "фуга",
    "отпадъци": "отпад", "отпадаци": "отпад", "отпадък": "отпад",
    "обръщане": "обръщане", "обръщания": "обръщане",
    "первази": "перваз", "подпрозоречни": "подпрозоречен",
    # Spelling variants / abbreviations seen in real КСС text (output.txt review):
    "шпахловка": "шпакловка", "шпахловъчна": "шпакловка", "шпакловки": "шпакловка",
    "гипскартон": "гипсокартон", "гипсокартона": "гипсокартон",
    "дост": "доставка", "дост.": "доставка", "монт": "монтаж", "монт.": "монтаж",
    "зидарии": "зидария", "зидариите": "зидария",
    "настилките": "настилка", "плочки": "плочка", "плочници": "плоча",
    "боядисвене": "боя", "боядисане": "боя",
    "изкопаване": "изкоп",
    "гипсокартонена": "гипсокартон",
    "дограмата": "дограма", "дограмите": "дограма",
    "перваз": "перваз", "каси": "каса",
}

TECH_RE = re.compile(
    r"(?:ф\s*\d+(?:[.,]\d+)?|Ø\s*\d+(?:[.,]\d+)?|\bC\s*\d{2}\s*/\s*\d{2}\b|\bB\s*\d{2,3}\b|"
    r"\d+(?:[.,]\d+)?\s*(?:мм|см|m2|m3|м2|м3|кв\.\s*м|куб\.\s*м)\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:kw|mw|kva|квт|мвт|ква)\b|"
    r"\b\d+(?:[.,]\d+)?\s*[xх×]\s*\d+(?:[.,]\d+)?(?:\s*[xх×]\s*\d+(?:[.,]\d+)?)?\b)", re.I)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).replace("\u00a0", " ")
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", s).strip()


def _norm_unit(u: Any) -> str | None:
    """Normalize a unit for comparison. Alias keys are matched after stripping
    spaces AND dots, so „куб.м."/„кв.м."/„л.м." hit „куб.м"/„кв.м"/„л.м.";
    unknown units fall back to dotless UPPERCASE (matches seed storage like
    „КОМПЛ" for „компл.")."""
    global _UNIT_ALIASES_NORM
    if _UNIT_ALIASES_NORM is None:
        _UNIT_ALIASES_NORM = {re.sub(r"[\s.]+", "", k.lower()): v for k, v in UNIT_ALIASES_DEFAULT.items()}
    s = re.sub(r"[\s.]+", "", _clean(u).lower())
    return _UNIT_ALIASES_NORM.get(s, s.upper() if s else None)


def _deconfuse_word(word: str) -> str:
    return word.translate(LAT2CYR) if re.search(r"[А-Яа-я]", word) else word


def _norm_token(word: str) -> str:
    w = _deconfuse_word(word.lower())
    if w in SYNONYMS:
        return SYNONYMS[w]
    # conservative suffix normalization for common Bulgarian inflections
    if len(w) >= 6:
        for suf in ("ата", "ите", "ите", "ия", "и", "а", "ове", "еви", "ски", "на", "ни"):
            if w.endswith(suf) and len(w) - len(suf) >= 5:
                w = w[:-len(suf)]
                break
    return w


def tokens(text: str) -> set[str]:
    """Meaning-bearing tokens. >=3 chars pass as before; 2-char terms are
    admitted only when they carry a fact — uppercase abbreviations in the
    source text (СК, ОК, ПС) or letter+digit specs (ф2, 1U) — matching the
    ERP retrieval-term admission rule."""
    out = set()
    for w in re.split(r"[^0-9A-Za-zА-Яа-я]+", _domain_norm(text)):
        if not w:
            continue
        nw = _norm_token(w)
        if len(nw) >= 3 and nw not in STOP:
            out.add(nw)
        elif (len(nw) == 2 and nw not in STOP and any(ch.isalpha() for ch in nw)
              and (any(ch.isdigit() for ch in nw) or w.isupper())):
            out.add(nw)
    return out


_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MIN_TERM_LENGTH = 3
_MAX_TERMS = 16


def retrieval_terms(text: str) -> list[str]:
    """Terms one description contributes to candidate retrieval.

    Ported from the ERP cost-match repository: the description's own words
    (>=3 chars, non-stopword) plus their folded twin (й->и) and stem, so
    Bulgarian inflection can't hide a corpus row. Two-character terms are
    admitted only when meaningful — an uppercase abbreviation (СК, ОК, ПС)
    or a letter glued to a digit (ф2, 1U); bare numbers stay out. Concept
    tokens from the evidence matcher widen the query onto the shared
    vocabulary the FTS surfaces carry. Order-preserving, capped at 16."""
    if not matcher:
        return [t for t in sorted(tokens(text)) if len(t) >= _MIN_TERM_LENGTH][:_MAX_TERMS]
    terms: dict[str, None] = {}
    for word in _WORD_RE.findall(text or ""):
        if len(word) >= _MIN_TERM_LENGTH and matcher.normalize_text(word) not in matcher._STOPWORDS:
            terms.setdefault(word, None)
            folded = matcher.normalize_text(word)
            if folded and folded != word.lower():
                terms.setdefault(folded, None)
            stem = matcher._stem(folded or word.lower())
            if len(stem) >= 4 and stem != word.lower() and stem != folded:
                terms.setdefault(stem, None)
        elif (len(word) == 2
              and matcher.normalize_text(word) not in matcher._STOPWORDS
              and any(ch.isalpha() for ch in word)
              and (any(ch.isdigit() for ch in word) or word.isupper())):
            terms.setdefault(word, None)
    for token in matcher.canonical_tokens(text):
        if len(token) >= _MIN_TERM_LENGTH:
            terms.setdefault(token, None)
    return list(terms)[:_MAX_TERMS]


def _concept_surfaces(text: str) -> set[str]:
    """Every surface form of each concept the text touches.

    The corpus writes catalog-speak (пердашена замазка), bills write
    estimator-speak (шлайфана настилка) — adding all surface forms of each
    detected concept to the FTS row bridges the two vocabularies without
    touching the displayed description."""
    if not matcher:
        return set()
    forms: set[str] = set()
    for tok in matcher.canonical_tokens(text):
        for form in matcher._CONCEPT_SYNONYMS.get(tok, ()):
            if len(form) >= 2:
                forms.add(form)
    return forms


def tech_tokens(text: str) -> set[str]:
    raw = set(m.group(0).lower().replace(" ", "") for m in TECH_RE.finditer(_clean(text)))
    return {_deconfuse_word(x) for x in raw}


_LAT2CYR_KEYS = set(map(chr, _deconfuse_word.__defaults__[0].keys())) if False else None


def _all_confusable_caps(w: str) -> bool:
    """True if word is all-uppercase and every letter has a Cyrillic lookalike in our map."""
    letters = [ch for ch in w if ch.isalpha()]
    return bool(letters) and w == w.upper() and all(
        ch in "ABCEHKMOPTXYacehikmoptxy" for ch in letters)


# Engineer-shorthand expansion: КСС writers use field abbreviations the
# catalogue stores under full names. Applied AFTER deconfuse (all-Cyrillic
# by then) in _domain_norm so query AND index tokenize identically.
_ABBR_SWAPS = (
    (re.compile(r"\bсвт\b", re.I), " кабел свт "),
    (re.compile(r"\bпвв\b", re.I), " кабел пвв "),
    (re.compile(r"\bтчп\b", re.I), " кабел тчп "),
    (re.compile(r"\bск\b", re.I), " спирателен кран "),
    (re.compile(r"\bвк\b", re.I), " водопроводен кран "),
    (re.compile(r"\bпс\b", re.I), " подов сифон "),
    (re.compile(r"\bкаб\.\s*скара\b", re.I), " кабелна скара "),
    (re.compile(r"\bокач\.\s*", re.I), " окачен "),
    (re.compile(r"\bводом\.\s*", re.I), " водомер "),
    (re.compile(r"\bдистанц\.\s*", re.I), " дистанционен "),
    (re.compile(r"\bотрито\b", re.I), " открито "),
    (re.compile(r"\bрогов\b", re.I), " ъглов "),
    (re.compile(r"\bохр\.\s*гарн\.\s*", re.I), " охранителна гарнитура "),
    (re.compile(r"\bчун\.\s*гърне\b", re.I), " чугунено гърне "),
    (re.compile(r"\bтр\.\s*", re.I), " тръба "),
    (re.compile(r"\bатм\b", re.I), " атмосфери "),
    (re.compile(r"(?:[pр]v[cс]|пвс)\s*тръ?б?\w*", re.I), " pvc тръба "),
    (re.compile(r"\bhl\b", re.I), " хидрант "),
)


def _domain_norm(text: str) -> str:
    """ONE input normalize pipeline for tokens/exact/fts:
    - strip trailing parenthetical unit markers („(М 2 )“, „(бр.)“)
    - normalize „в/у“ → „върху“
    - deconfuse: per-word when the TEXT contains Cyrillic, or the word itself is
      either mixed-with-Cyrillic or fully composed of confusable Latin capitals."""
    import re as _re
    s = _clean(text)
    s = _re.sub(r"\((?:м\s*2|м\s*3|бр\.?|брой|буч|кг|т|л\.\s*м\.?|м|час|ч|1000\s*м\s*2)[^)]*\)\s*$", "", s, flags=_re.I)
    # deconfuse FIRST (B/У has a Latin B), then phrase swaps on normalized text
    has_cyr = bool(_re.search(r"[А-Яа-я]", s))
    out = []
    for w in _re.split(r"(\W+)", s):
        if w and _re.search(r"[0-9A-Za-zА-Яа-я]", w):
            if has_cyr or _re.search(r"[А-Яа-я]", w) or _all_confusable_caps(w):
                w = w.translate(LAT2CYR)
        out.append(w)
    s = _re.sub(r"\s+", " ", "".join(out)).strip()
    s = _re.sub(r"(?i)\bв\s*/\s*у\b", " върху ", s)
    for rx, rep in _ABBR_SWAPS:
        s = rx.sub(rep, s)
    return _re.sub(r"\s+", " ", s).strip()


def exact_norm(text: str) -> str:
    """Canonical token sequence preserving short technical tokens/numbers for exact comparisons."""
    parts = []
    for raw in re.split(r"[^0-9A-Za-zА-Яа-я]+", _domain_norm(text)):
        if not raw:
            continue
        w = _deconfuse_word(raw.lower())
        if re.search(r"[0-9]", w) or len(w) < 4:
            parts.append(w)
        else:
            parts.append(_norm_token(w))
    return " ".join(parts)


def _source_for_file(name: str) -> dict[str, str]:
    stem = Path(name).stem
    key = stem[len("costdb_seed_"):] if stem.startswith("costdb_seed_") else stem
    if stem.startswith("costdb_seed_user_"):
        return {"source_key": f"user_{key[len('user_'):] if key.startswith('user_') else key}",
                "source_type": "company_actual",
                "display_name": f"Собствени цени (качени от потребителя) — {stem}",
                "as_of": _now()[:10]}
    return SOURCE_META.get(name, {
        "source_key": f"imported_{key}",
        "source_type": "imported_json",
        "display_name": f"Imported JSON — {name}",
        "as_of": ""
    })




def _normalized_search_text(*parts: Any, extra_json: Any = None) -> str:
    """Normalize Cyrillic/Latin confusables, synonym families, concept surfaces
    and curated bill_terms for FTS. The displayed description stays untouched —
    this is the retrieval surface only."""
    vals = []
    for part in parts:
        text = _clean(part)
        # unicode61 does no case folding: ALL-CAPS catalogue rows (КРАН СФЕР.)
        # must index lowercase so a lowercase query term reaches them.
        vals.append(text.lower())
        vals.append(" ".join(sorted(tokens(text))))
        vals.append(" ".join(sorted(tech_tokens(text))))
        vals.append(" ".join(sorted(_concept_surfaces(text))))
    if extra_json:
        try:
            terms = json.loads(extra_json).get("bill_terms") or []
        except Exception:
            terms = []
        alias = " ".join(t for t in terms if isinstance(t, str))
        if alias:
            vals.append(alias.lower())
            vals.append(" ".join(sorted(tokens(alias))))
    return " ".join(v for v in vals if v)


def _rebuild_fts(conn: sqlite3.Connection):
    """Full FTS rebuild with the normalized surface (terms, specs, concept
    surfaces, bill_terms from extra_json)."""
    conn.execute("DELETE FROM cost_items_fts")
    rows = conn.execute("SELECT id,name,desc,category,section,extra_json FROM cost_items").fetchall()
    for r in rows:
        conn.execute("INSERT INTO cost_items_fts(id,search_text) VALUES(?,?)",
                     (r["id"], _normalized_search_text(r["name"], r["desc"], r["category"], r["section"],
                                                     extra_json=r["extra_json"])))
    conn.commit()


def tech_tokens(text: str) -> set[str]:
    return {_deconfuse_word(m.group(0).lower().replace(" ", "")) for m in TECH_RE.finditer(_clean(text))}


def _ensure_fts_shape(conn: sqlite3.Connection):
    # DB v2 uses a standalone normalized FTS table. Rebuild when upgrading from v1.
    v = conn.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()
    old_v = int(v[0]) if v and str(v[0]).isdigit() else 0
    if old_v != DB_VERSION:
        conn.execute("DROP TRIGGER IF EXISTS cost_items_ai")
        conn.execute("DROP TRIGGER IF EXISTS cost_items_ad")
        conn.execute("DROP TRIGGER IF EXISTS cost_items_au")
        conn.execute("DROP TABLE IF EXISTS cost_items_fts")
        conn.executescript(SCHEMA_SQL)
        conn.execute("INSERT INTO meta(key,value) VALUES('db_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(DB_VERSION),))
        _rebuild_fts(conn)
    else:
        # Repair/normalize missing FTS rows without duplicating existing ones.
        count_cost = conn.execute("SELECT COUNT(*) FROM cost_items").fetchone()[0]
        count_fts = conn.execute("SELECT COUNT(*) FROM cost_items_fts").fetchone()[0]
        if count_cost != count_fts:
            _rebuild_fts(conn)

def _iter_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("rows"), list):
            return [x for x in data["rows"] if isinstance(x, dict)]
        if isinstance(data.get("prices"), list):
            return [x for x in data["prices"] if isinstance(x, dict)]
    return []


def _ensure_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('db_version',?)", (str(DB_VERSION),))
    # additive column migration (runs.cost_env_id pins each run to an immutable env version)
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN cost_env_id INTEGER")
    except sqlite3.OperationalError:
        pass  # вече съществува
    conn.commit()


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    if db_path == ":memory:":
        conn = sqlite3.connect(":memory:", timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.create_function("norm_search_text", 4, lambda a, b, c, d: _normalized_search_text(a, b, c, d))
        _ensure_schema(conn)
        _ensure_fts_shape(conn)
        return conn
    p = Path(db_path or os.environ.get(DB_ENV) or "tenderops.sqlite3").resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.create_function("norm_search_text", 4, lambda a, b, c, d: _normalized_search_text(a, b, c, d))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    _ensure_schema(conn)
    _ensure_fts_shape(conn)
    return conn


def db_path_for(demo_dir: str | Path | None = None) -> Path:
    env = os.environ.get(DB_ENV)
    if env:
        return Path(env).resolve()
    write_base = os.environ.get("TENDEROPS_WRITEBASE")
    if write_base:
        return (Path(write_base) / "tenderops.sqlite3").resolve()
    if demo_dir:
        return (Path(demo_dir).resolve().parent.parent / "tenderops.sqlite3").resolve()
    return Path("tenderops.sqlite3").resolve()


def _source_key_for(item: dict[str, Any], filename: str) -> tuple[str, str, str, str]:
    meta = _source_for_file(filename)
    origin = item.get("origin") or {}
    okind = str(origin.get("kind") or "").strip()
    if okind == "supplier_web_anchor":
        typ = "reference_web"
    elif okind == "won_tender":
        typ = "awarded_tender"
    elif okind == "current_year_kss":
        typ = "current_kss"
    elif okind == "supplier_quote":
        typ = "supplier_quote"
    elif okind == "imported_workbook":
        typ = meta["source_type"]
    else:
        typ = meta["source_type"]
    return meta["source_key"], typ, meta["display_name"], meta["as_of"]


def _item_from_json(item: dict[str, Any], filename: str) -> dict[str, Any] | None:
    money = item.get("money") or {}
    origin = item.get("origin") or {}
    validity = item.get("validity") or {}
    amount = money.get("amount")
    if not isinstance(amount, (int, float)) or float(amount) <= 0:
        return None
    currency = str(money.get("currency") or "EUR").upper()
    eur = float(amount) / EURBGN if currency == "BGN" else float(amount)
    vat = bool(money.get("vatIncluded"))
    origin_kind = str(origin.get("kind") or "estimate_book")
    status = str(item.get("status") or "active")
    # 2026-09-08 owner order: web anchors MAY be active when the seed explicitly says so
    # (the seed file is the audit boundary — status lives there, loudly). The default for
    # a web anchor without an explicit status remains pending_review; unknown statuses too.
    if status not in {"active", "pending_review", "retired"}:
        status = "pending_review"
    elif origin_kind == "supplier_web_anchor" and "status" not in item:
        status = "pending_review"
    name = _clean(item.get("name") or item.get("desc"))
    desc = _clean(item.get("desc") or item.get("name"))
    if len(desc) < 3:
        return None
    components = item.get("components") or {}
    code = _clean(item.get("code"))
    if not code:
        ref = _clean(origin.get("ref"))
        m = re.search(r"(?:SEK[-\s]?)(СЕК\d+[.\-]\d+|\d+[.\-]\d+)$", ref, re.I)
        if m:
            code = m.group(1)
    extra = {
        "spec": item.get("spec") or {},
        "components": components,
        "notes": item.get("notes") or origin.get("note") or "",
        "raw_source_file": filename,
        "source_record": item.get("source_record") or {},
    }
    return {
        "id": _clean(item.get("id")) or None,
        "name": name,
        "desc": desc,
        "category": _clean(item.get("category")),
        "section": _clean(item.get("section")),
        "unit": _norm_unit(item.get("unit")),
        "amount_eur": round(eur, 8),
        "original_amount": float(amount),
        "currency": currency,
        "vat_included": vat,
        "as_of": str(money.get("asOf") or validity.get("from") or "")[:32],
        "derived_from_bgn": money.get("derivedFromBgn"),
        "origin_kind": origin_kind,
        "origin_ref": _clean(origin.get("ref")),
        "origin_note": _clean(origin.get("note")),
        "validity_from": _clean(validity.get("from")),
        "validity_to": validity.get("to"),
        "review_by": _clean(validity.get("reviewBy")),
        "status": status,
        "region": _clean(item.get("region")),
        "code": code,
        "extra_json": json.dumps(extra, ensure_ascii=False, separators=(",", ":")),
    }


def sync_json_sources(demo_dir: str | Path, db_path: str | Path | None = None, force: bool = False) -> dict[str, Any]:
    """Idempotently import supported JSON files into SQLite. JSON remains read-only source boundary."""
    demo = Path(demo_dir).resolve()
    conn = connect(db_path or db_path_for(demo))
    stats = {"sources": [], "added": 0, "replaced": 0, "skipped": 0}
    try:
        candidates = []
        for name in SOURCE_META:
            p = demo / name
            if p.exists():
                candidates.append(p)
        # Include any explicit additional imported cost seeds following the same naming convention.
        for p in sorted(x for x in demo.glob("costdb_seed*.json") if not x.name.endswith(".audit.json")):
            if p not in candidates:
                candidates.append(p)

        present_keys = []
        for p in candidates:
            source = _source_for_file(p.name)
            present_keys.append(source["source_key"])
            sha = _hash_file(p)
            old = conn.execute("SELECT file_sha256,source_key FROM cost_sources WHERE source_key=?", (source["source_key"],)).fetchone()
            if old and old["file_sha256"] == sha and not force:
                stats["skipped"] += 1
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception as ex:
                raise RuntimeError(f"Invalid JSON source {p}: {ex}") from ex
            rows = _iter_rows(data)

            source_key, source_type, display_name, as_of = source["source_key"], source["source_type"], source["display_name"], source["as_of"]
            for item in rows:
                inferred = _source_key_for(item, p.name)
                source_type = inferred[1] if inferred[1] else source_type
                display_name = inferred[2] or display_name
                as_of = inferred[3] or as_of
                break
            conn.execute("""INSERT INTO cost_sources(source_key,source_type,display_name,file_name,file_sha256,as_of,active,metadata_json,imported_at)
                            VALUES(?,?,?,?,?, ?,1,?,?)
                            ON CONFLICT(source_key) DO UPDATE SET source_type=excluded.source_type,display_name=excluded.display_name,
                            file_name=excluded.file_name,file_sha256=excluded.file_sha256,as_of=excluded.as_of,metadata_json=excluded.metadata_json,
                            imported_at=excluded.imported_at""",
                         (source_key, source_type, display_name, p.name, sha, as_of,
                          json.dumps({"file": p.name}, ensure_ascii=False), _now()))
            conn.execute("DELETE FROM cost_items WHERE source_key=?", (source_key,))
            added_here = 0
            for _i, item in enumerate(rows):
                rec = _item_from_json(item, p.name)
                if not rec:
                    continue
                if not rec["unit"]:
                    # Respect the cost-item schema: unknown unit is not admitted silently.
                    continue
                rid = rec["id"] or f"{source_key}-{_i}-{hashlib.sha1((rec['desc'] + '|' + str(rec['unit']) + '|' + str(rec['origin_ref']) + '|' + str(rec['amount_eur'])).encode('utf-8')).hexdigest()[:16]}"
                conn.execute("""INSERT INTO cost_items(
                    id,source_key,name,desc,category,section,unit,amount_eur,original_amount,currency,vat_included,as_of,
                    derived_from_bgn,origin_kind,origin_ref,origin_note,validity_from,validity_to,review_by,status,region,code,extra_json,imported_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid, source_key, rec["name"], rec["desc"], rec["category"], rec["section"], rec["unit"],
                     rec["amount_eur"], rec["original_amount"], rec["currency"], int(rec["vat_included"]), rec["as_of"],
                     rec["derived_from_bgn"], rec["origin_kind"], rec["origin_ref"], rec["origin_note"], rec["validity_from"],
                     rec["validity_to"], rec["review_by"], rec["status"], rec["region"], rec["code"], rec["extra_json"], _now()))
                added_here += 1
            stats["added"] += added_here
            stats["replaced"] += added_here if old else 0
            stats["sources"].append({"source": source_key, "rows": added_here, "sha256": sha, "file": p.name})
        # prune sources whose seed file disappeared (remove file -> remove source rows);
        # skipped (unchanged) sources still exist on disk and must survive — only files
        # absent from this run's candidate set may be pruned.
        active_keys = [s["source"] for s in stats["sources"]]
        keep_keys = sorted(set(present_keys) | set(active_keys))
        if keep_keys:
            marks = ",".join("?" * len(keep_keys))
            gone = conn.execute(f"SELECT source_key FROM cost_sources WHERE source_key NOT IN ({marks})",
                                keep_keys).fetchall()
            for g in gone:
                conn.execute("DELETE FROM cost_items WHERE source_key=?", (g["source_key"],))
                conn.execute("DELETE FROM cost_sources WHERE source_key=?", (g["source_key"],))
                conn.execute("INSERT INTO audit_events (at,actor,action,detail) VALUES (?,?,?,?)",
                             (_now(), "costdb", "prune_source", f"removed {g['source_key']}"))
            stats["pruned"] = [g["source_key"] for g in gone]
        # Curated bill-term aliases land before the FTS rebuild so the alias
        # surface reaches retrieval; display text is never touched.
        try:
            if corpus_aliases:
                stats["aliased_rows"] = corpus_aliases.apply(conn)
        except Exception as aex:
            stats["alias_error"] = str(aex)
        conn.commit()
        _ensure_fts_shape(conn)
        # Refresh FTS text ONLY when the corpus changed this run — the rebuild walks
        # every row; on an all-skipped sync it is pure startup tax ("ages to initialize").
        if stats["added"] > 0 or stats.get("pruned") or stats.get("aliased_rows"):
            _rebuild_fts(conn)
        return stats
    finally:
        conn.close()


def apply_corpus_aliases(db_path: str | Path | None = None) -> int:
    """Re-apply curated bill_terms to corpus rows and rebuild the FTS surface.

    Standalone entry point for post-import patching; idempotent (union merge).
    Returns rows patched."""
    conn = connect(db_path)
    try:
        n = corpus_aliases.apply(conn) if corpus_aliases else 0
        conn.commit()
        _rebuild_fts(conn)
        return n
    finally:
        conn.close()


def source_registry(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute("SELECT source_key,source_type,display_name,file_name,file_sha256,as_of,active,metadata_json,imported_at FROM cost_sources ORDER BY source_key")]


def stats(db_path: str | Path | None = None) -> dict[str, Any]:
    with connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) c FROM cost_items").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) c FROM cost_items WHERE status='active'").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) c FROM cost_items WHERE status='pending_review'").fetchone()[0]
        sources = conn.execute("SELECT COUNT(*) c FROM cost_sources WHERE active=1").fetchone()[0]
        return {"db_version": DB_VERSION, "cost_items": total, "active": active, "pending_review": pending, "sources": sources}


def export_cost_items(db_path: str | Path, out_path: str | Path, include_pending: bool = True):
    with connect(db_path) as conn:
        where = "" if include_pending else "WHERE status='active'"
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM cost_items {where} ORDER BY source_key,id")]
    out = []
    for r in rows:
        money = {"amount": r["original_amount"], "currency": r["currency"], "vatIncluded": bool(r["vat_included"]), "asOf": r["as_of"]}
        if r["derived_from_bgn"] is not None:
            money["derivedFromBgn"] = r["derived_from_bgn"]
        extra = json.loads(r["extra_json"] or "{}")
        item = {
            "id": r["id"], "name": r["name"], "desc": r["desc"], "category": r["category"], "section": r["section"],
            "unit": r["unit"], "money": money,
            "origin": {"kind": r["origin_kind"], "ref": r["origin_ref"], "note": r["origin_note"]},
            "validity": {"from": r["validity_from"], "to": r["validity_to"], "reviewBy": r["review_by"]},
            "status": r["status"], "region": r["region"],
        }
        if r["code"]:
            item["code"] = r["code"]
        if extra.get("spec"):
            item["spec"] = extra["spec"]
        if extra.get("components"):
            item["components"] = extra["components"]
        out.append(item)
    Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(out)


_SPEC_FTS_RE = re.compile(
    r"(?:ф|ø|dn|дн)\s*\d+(?:[.,]\d+)?|pn\s*\d+|\d{2,3}\s*[xх×]\s*\d{2,3}", re.I)


def _fts_query(text: str) -> str:
    """FTS5 query from retrieval_terms: >=3-char terms prefix-match, 2-char
    abbreviations/specs match whole tokens only — the FTS tokenizer gives the
    word boundary the old substring logic could not."""
    terms = retrieval_terms(text)
    if not terms:
        return ""
    parts = []
    for t in terms[:_MAX_TERMS]:
        t = t.replace('"', "").strip().lower()
        if not t:
            continue
        parts.append(f'"{t}"' if len(t) < 3 else f"{t}*")
    return " OR ".join(parts)


def _fts_query_legacy(text: str) -> str:
    toks = [t for t in tokens(text) if len(t) >= 3]
    # Spec terms live <4 chars so tokens() drops them, yet the FTS index stores
    # them (ф63, 20х16, pn10). Re-inject from raw text — без тях тръбни редове
    # търсят само „доставка" и връщат целия каталог.
    for m in _SPEC_FTS_RE.finditer(text or ""):
        t = re.sub(r"\s+", "", m.group(0).lower())
        if t and t not in toks:
            toks.append(t)
    if not toks:
        return ""
    return " OR ".join('"' + t.replace('"','') + '*"' for t in toks[:20])


def search_candidates(db_path: str | Path, query: str, unit: str | None = None, limit: int = 40, include_pending: bool = False, code: str | None = None, origin_kind: str | None = None) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        u = _norm_unit(unit)
        if code and not origin_kind:
            rows = conn.execute("SELECT * FROM cost_items WHERE code=? AND status != 'retired' AND (? OR status='active') ORDER BY status='active' DESC LIMIT ?", (code, int(include_pending), limit)).fetchall()
            if rows:
                return [dict(r) for r in rows]
        q = _fts_query(query)
        if q:
            rows = conn.execute("""SELECT c.* FROM cost_items_fts f JOIN cost_items c ON c.id=f.id
                                  WHERE cost_items_fts MATCH ? AND c.status != 'retired' AND (? OR c.status='active')
                                  ORDER BY bm25(cost_items_fts) LIMIT ?""", (q, int(include_pending), max(limit * 4, 40))).fetchall()
        else:
            # Fallback on the normalized FTS text (raw + deconfused tokens), so a pure-Cyrillic
            # query still hits stored rows whose desc contains Latin lookalikes inside words.
            like = f"%{_clean(query).lower()}%"
            rows = conn.execute("""SELECT c.* FROM cost_items_fts f JOIN cost_items c ON c.id=f.id
                                  WHERE lower(f.search_text) LIKE ? AND c.status != 'retired' AND (? OR c.status='active')
                                  LIMIT ?""", (like, int(include_pending), max(limit * 4, 40))).fetchall()
        # Unit mismatch must NOT delete the candidate: right work in a different
        # unit (м↔м², м²↔м³) is a review-level reference the scorer penalizes
        # (-20) and the REF lane flags — a hard drop made real corpus prices
        # invisible (силиконова мазилка м² against a м row never got scored).
        out = [dict(r) for r in rows]
        if origin_kind:
            kinds = set(origin_kind) if isinstance(origin_kind, (list, tuple, set)) else {origin_kind}
            out = [r for r in out if r.get("origin_kind") in kinds]
        return out[:limit]
    finally:
        conn.close()


def get_active_rows(db_path: str | Path) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM cost_items WHERE status='active' ORDER BY source_key,id").fetchall()
    return [dict(r) for r in rows]


# ---------------- Gate 1 #2: immutable cost environment versions ----------------

def ensure_env_version(db_path, note=""):
    """If current source SHAs differ from the latest version's snapshot,
    INSERT a new immutable version. Never mutates an old version row.
    Returns the current env version id."""
    conn = connect(db_path)
    try:
        cur = conn.execute("SELECT source_key,file_sha256,active FROM cost_sources").fetchall()
        snap = {r["source_key"]: {"sha256": r["file_sha256"], "active": r["active"]} for r in cur}
        last = conn.execute("SELECT id,sources_json FROM cost_env_versions ORDER BY id DESC LIMIT 1").fetchone()
        if last and json.loads(last["sources_json"]) == snap:
            return last["id"]
        conn.execute("INSERT INTO cost_env_versions (created_at,sources_json,note) VALUES (?,?,?)",
                     (_now(), json.dumps(snap, ensure_ascii=False), note or "source set changed"))
        vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO audit_events (at,actor,action,detail) VALUES (?,?,?,?)",
                     (_now(), "costdb", "env_version", f"cost_env_version #{vid} created ({len(snap)} sources)"))
        conn.commit()
        return vid
    finally:
        conn.close()


def current_env_id(db_path):
    conn = connect(db_path)
    try:
        r = conn.execute("SELECT id FROM cost_env_versions ORDER BY id DESC LIMIT 1").fetchone()
        return r[0] if r else None
    finally:
        conn.close()


# ---------------- Gate 1 #3: human resolutions in the canonical store ----------------

def upsert_resolution(db_path, tender_id, boq_key, note, actor="operator", rationale="", evidence=None):
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO human_resolutions (tender_id,boq_key,note,actor,at,rationale,evidence_json)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(tender_id,boq_key) DO UPDATE SET note=excluded.note, actor=excluded.actor,"
            " at=excluded.at, rationale=excluded.rationale, evidence_json=excluded.evidence_json",
            (tender_id, boq_key, note, actor, _now(), rationale,
             json.dumps(evidence, ensure_ascii=False) if evidence else None))
        conn.commit()
    finally:
        conn.close()
    if telemetry:
        try:
            telemetry.log_event(
                telemetry.EventType.HUMAN_GATE_RESOLVED,
                message=f"Human gate resolved for line '{boq_key}': {note}",
                tender_id=tender_id,
                actor_id=actor,
                actor_type="user" if actor == "user" else "operator",
                payload={"boq_key": boq_key, "note": note, "rationale": rationale, "evidence": evidence},
                db_path=db_path
            )
        except Exception:
            pass


def get_resolutions(db_path, tender_id=None):
    """UI-compatible shape: {key: {note,by,at,...}}.
    tender_id given (tender context): ONLY that tender's rows PLUS legacy global rows
    (tender_id NULL from pre-scoping days or -1 sentinel from legacy imports) — other
    tenders' resolutions and est:: rows (estimation door) never leak across (canon:
    една поръчка не наследява чужди решения)."""
    conn = connect(db_path)
    try:
        out = {}
        for r in conn.execute("SELECT tender_id,boq_key,note,actor,at,rationale FROM human_resolutions ORDER BY id"):
            k = r["boq_key"]
            if tender_id is not None:
                if str(k).startswith("est::"):
                    continue
                if r["tender_id"] not in (None, -1, tender_id):
                    continue
            out[k] = {"note": r["note"], "by": r["actor"], "at": r["at"], "rationale": r["rationale"]}
        return out
    finally:
        conn.close()


LEGACY_TENDER_ID = -1  # sentinel: legacy import rows (NULL never dedupes under UNIQUE)


def import_resolutions_json(db_path, json_path):
    """One-shot legacy import: data/demo/resolutions.json -> table. Idempotent (upsert).
    Legacy rows carry the -1 sentinel tender (SQLite treats NULLs as DISTINCT, so NULL
    could never fire the UNIQUE upsert — that was the restart-duplication defect)."""
    p = Path(json_path)
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8-sig")) or {}
    n = 0
    for key, v in data.items():
        if not isinstance(v, dict):
            continue
        upsert_resolution(db_path, LEGACY_TENDER_ID, key, str(v.get("note") or ""), str(v.get("by") or "operator"),
                          str(v.get("rationale") or ""))
        n += 1
    return n


def sweep_interrupted_runs(db_path, note="server restart"):
    """Startup/reset hygiene: runs left without finished_at (kill mid-run, crash) are
    zombies — jobstatus would poll them forever. Close them loudly as INTERRUPTED."""
    conn = connect(db_path)
    try:
        cur = conn.execute("UPDATE runs SET state='INTERRUPTED', finished_at=?, note=? WHERE finished_at IS NULL",
                           (_now(), note))
        conn.commit()
        n = cur.rowcount
    finally:
        conn.close()
    if telemetry and n:
        try:
            telemetry.log_event("RUNS_SWEEP", message=f"{n} interrupted run(s) closed: {note}",
                                component="costdb", severity="WARN", db_path=db_path)
        except Exception:
            pass
    return n


# ---------------- runs (Gate 1 #3 records the env pin per run) ----------------

def run_open(db_path, tender_id, cost_env_id=None):
    if cost_env_id is None:
        cost_env_id = ensure_env_version(db_path)
    conn = connect(db_path)
    conn.execute("INSERT INTO runs (tender_id,started_at,state,cost_env_id) VALUES (?,?,?,?)",
                 (tender_id, _now(), "RUNNING", cost_env_id))
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit(); conn.close()
    if telemetry:
        try:
            telemetry.log_event(
                telemetry.EventType.RUN_CREATED,
                message=f"Run #{rid} created for tender {tender_id}",
                run_id=rid,
                tender_id=tender_id,
                state_after="RUNNING",
                status="STARTED",
                payload={"cost_env_id": cost_env_id},
                db_path=db_path
            )
        except Exception:
            pass
    return rid


def pin_run_env(db_path, run_id, env_id):
    """Re-pin a run to the env version in force at its pricing moment (post-sync)."""
    conn = connect(db_path)
    conn.execute("UPDATE runs SET cost_env_id=? WHERE id=?", (env_id, run_id))
    conn.commit(); conn.close()


def run_event(db_path, run_id, state, detail=""):
    try:
        conn = connect(db_path)
        conn.execute("INSERT INTO run_events (run_id,at,state,detail) VALUES (?,?,?,?)",
                     (run_id, _now(), state, detail))
        conn.execute("UPDATE runs SET state=? WHERE id=?", (state, run_id))
        conn.commit(); conn.close()
    except Exception:
        pass  # журналът е одитен слой — спирането му не спира производството
    if telemetry:
        try:
            ev_type = telemetry.EventType.RUN_STAGE_STARTED
            if state in ("PACKED", "COMPLETED"):
                ev_type = telemetry.EventType.RUN_COMPLETED
            elif state in ("FAILED", "ERROR"):
                ev_type = telemetry.EventType.RUN_FAILED
            elif state == "CANCELLED":
                ev_type = telemetry.EventType.RUN_CANCELLED
            telemetry.log_event(
                ev_type,
                message=detail or f"Run #{run_id} entered state {state}",
                run_id=run_id,
                status=state,
                state_after=state,
                payload={"detail": detail, "raw_state": state},
                db_path=db_path
            )
        except Exception:
            pass


def run_close(db_path, run_id, state, note=""):
    conn = connect(db_path)
    prev = conn.execute("SELECT state FROM runs WHERE id=?", (run_id,)).fetchone()
    conn.execute("UPDATE runs SET finished_at=?, state=?, note=? WHERE id=?",
                 (_now(), state, note, run_id))
    conn.commit(); conn.close()
    if telemetry:
        try:
            # Journal already registered this terminal state (run_event) → don't emit
            # a second RUN_FAILED/RUN_CANCELLED for the same transition.
            if prev and prev["state"] == state and state not in ("PACKED", "COMPLETED"):
                return
            is_ok = state in ("PACKED", "COMPLETED")
            telemetry.log_event(
                telemetry.EventType.RUN_COMPLETED if is_ok else telemetry.EventType.RUN_FAILED,
                message=note or f"Run #{run_id} closed with state {state}",
                run_id=run_id,
                state_after=state,
                status="SUCCESS" if is_ok else "FAILED",
                payload={"note": note, "final_state": state},
                db_path=db_path
            )
        except Exception:
            pass


def record_outcome(db_path, tender_id, bid_amount=None, submitted_at=None, won=None,
                   awarded_amount=None, actual_cost=None, note=""):
    margin = None
    if bid_amount and actual_cost:
        margin = round((bid_amount - actual_cost) / bid_amount, 4)
    conn = connect(db_path)
    conn.execute("INSERT INTO outcomes (tender_id,bid_amount,submitted_at,won,awarded_amount,actual_cost,margin,note)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 (tender_id, bid_amount, submitted_at, won, awarded_amount, actual_cost, margin, note))
    conn.commit(); conn.close()
    if telemetry:
        try:
            telemetry.log_event(
                telemetry.EventType.HISTORY_RECORD_CREATED,
                message=f"Tender #{tender_id} outcome recorded: bid={bid_amount}, won={won}",
                tender_id=tender_id,
                payload={"bid_amount": bid_amount, "submitted_at": submitted_at, "won": won, "awarded_amount": awarded_amount, "actual_cost": actual_cost, "margin": margin, "note": note},
                db_path=db_path
            )
        except Exception:
            pass


def save_fingerprint(db_path, tender_id, tokens, rows_n, priced_n, total_eur):
    conn = connect(db_path)
    conn.execute("INSERT INTO tender_fingerprints (tender_id,tokens_json,rows,priced,total_eur,at)"
                 " VALUES (?,?,?,?,?,?) ON CONFLICT(tender_id) DO UPDATE SET"
                 " tokens_json=excluded.tokens_json, rows=excluded.rows, priced=excluded.priced,"
                 " total_eur=excluded.total_eur, at=excluded.at",
                 (tender_id, json.dumps(sorted(tokens), ensure_ascii=False), rows_n, priced_n, total_eur, _now()))
    conn.commit(); conn.close()


def similar_tenders(db_path, tokens, exclude_id=None, limit=5):
    """Jaccard over stored КСС fingerprints. Cheap memory: 'прилича на...'"""
    mine = set(tokens)
    if not mine:
        return []
    conn = connect(db_path)
    rows = conn.execute("SELECT tender_id,tokens_json,rows,priced,total_eur,at FROM tender_fingerprints").fetchall()
    conn.close()
    out = []
    for tid, tj, rn, pn, tot, at in rows:
        if exclude_id and tid == exclude_id:
            continue
        other = set(json.loads(tj or "[]"))
        if not other:
            continue
        jac = len(mine & other) / max(1, len(mine | other))
        if jac >= 0.12:
            out.append({"tender_id": tid, "similarity": round(jac, 3), "rows": rn,
                        "priced": pn, "total_eur": tot, "at": at})
    out.sort(key=lambda x: -x["similarity"])
    return out[:limit]


def get_outcome(db_path, tender_id):
    conn = connect(db_path)
    r = conn.execute("SELECT * FROM outcomes WHERE tender_id=? ORDER BY id DESC LIMIT 1", (tender_id,)).fetchone()
    conn.close()
    return dict(r) if r else None


def record_match(db_path, tender_id, boq_line, chosen, method, score, margin, confidence, evidence):
    """Persist one pricing decision + candidates. IDEMPOTENT: the decision's identity is
    deterministic (tender_id × boq_key × chosen_item_id × method). Re-running the same
    decision refreshes it in place instead of appending a logical duplicate."""
    conn = connect(db_path)
    try:
        chosen_s = str(chosen) if chosen is not None else None
        row = conn.execute(
            "SELECT id FROM matches WHERE tender_id=? AND boq_key=? AND chosen_item_id IS ? AND method=?",
            (tender_id, boq_line.get("key"), chosen_s, method)).fetchone()
        evj = json.dumps(evidence, ensure_ascii=False)
        if row:
            mid = row["id"]
            conn.execute(
                "UPDATE matches SET boq_desc=?,boq_unit=?,boq_qty=?,score=?,margin=?,confidence=?,evidence_json=?,at=? WHERE id=?",
                (boq_line.get("desc"), boq_line.get("unit"), boq_line.get("qty"), score, margin,
                 confidence, evj, _now(), mid))
            conn.execute("DELETE FROM match_candidates WHERE match_id=?", (mid,))
        else:
            conn.execute(
                "INSERT INTO matches (tender_id,boq_key,boq_desc,boq_unit,boq_qty,chosen_item_id,method,score,margin,confidence,evidence_json,at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (tender_id, boq_line.get("key"), boq_line.get("desc"), boq_line.get("unit"), boq_line.get("qty"),
                 chosen_s, method, score, margin, confidence, evj, _now()))
            mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for rank, c in enumerate((evidence or {}).get("top_candidates") or [], 1):
            conn.execute("INSERT INTO match_candidates (match_id,item_id,rank,score,detail_json) VALUES (?,?,?,?,?)",
                         (mid, str(c.get("id")), rank, c.get("score"), json.dumps(c, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    if telemetry:
        try:
            telemetry.log_event(
                telemetry.EventType.PRICE_DECISION,
                message=f"BOQ line '{boq_line.get('key')}' priced via {method} with score {score}",
                tender_id=tender_id,
                payload={
                    "boq_key": boq_line.get("key"),
                    "boq_desc": boq_line.get("desc"),
                    "boq_unit": boq_line.get("unit"),
                    "boq_qty": boq_line.get("qty"),
                    "chosen_item_id": chosen_s,
                    "method": method,
                    "score": score,
                    "margin": margin,
                    "confidence": confidence,
                    "candidate_count": len((evidence or {}).get("top_candidates") or [])
                },
                db_path=db_path
            )
        except Exception:
            pass


def priority_for(row: dict[str, Any]) -> float:
    base = ORIGIN_PRIORITY.get(row.get("origin_kind"), 40)
    src = str(row.get("source_key") or "")
    if src.startswith("sek_"):
        base = max(base, 82)
    return float(base)



