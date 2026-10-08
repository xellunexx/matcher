# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""International, explainable text matcher for cost-database lookup.

This module is the pure, database-free core of cost matching. It compares a
free-text BoQ item or CAD element description against candidate cost-database
entries and returns a confidence score plus a plain-language explanation of
why that score was reached.

Why it exists
-------------
Construction descriptions arrive in many languages and unit systems. A wall
labelled "Stahlbetonwand" (de), "mur en beton arme" (fr), "muro de hormigon
armado" (es) or "железобетонная стена" (ru) should all match the same
"reinforced concrete wall" cost item. This matcher folds accents, normalises
units across metric and imperial, and maps a curated multilingual synonym set
onto shared concept tokens so the comparison happens on meaning, not spelling.

How the confidence score is computed
------------------------------------
The score is a value in ``[0.0, 1.0]`` built from transparent factors,
all returned in :class:`MatchScore.factors` so a user can audit any result.

Evidence, not coverage, drives the score. A bill line's words are claims;
the ones a corpus row actually answers are the evidence, and the ones no
row answers explain nothing - "стълби" in "Полагане на гранит-стълби" says
where the granite goes, it does not make the granite price weaker. So:

    base = 0.40 + 0.30 * min(shared_content, 2) + 0.05 * extra
           + 0.15 * shared_action_family + 0.10 * shared_spec_value
    confidence = round(base * unit_factor * action_factor, 4)

with these honest guards:

1. ``no_content_overlap`` - zero shared content tokens scores 0.0.
   Process words ("доставка и монтаж") never count as content.
2. ``thin_content_coverage`` - exactly one shared noun with no action or
   spec corroboration stays below the review floor (the patch-panel
   priced as lintel panels is a bridge, not evidence).
3. ``unit_factor`` / ``action_factor`` / ``spec_conflict`` - multipliers
   that only reduce: incompatible units, opposite job families
   (demolition vs installation), or declared specs that disagree.

``query_coverage``, ``term_overlap``, ``matched_tokens`` and
``candidate_coverage`` are still reported as factors - coverage informs
the audit and breaks ties (the simpler, fully-explained row wins), it
just no longer dilutes the score.

A normalised exact-string equality short-circuits to ``1.0`` before any of
the above. Scores at or above :data:`HIGH_CONFIDENCE` are treated as
confident; between :data:`REVIEW_CONFIDENCE` and that as needs-review; below
it as no confident match, in which case a short hint tells the user what to
try next.

Everything here is deterministic and free of I/O, so it is trivially unit
testable and safe to run on any input, including regex metacharacters.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

# Vendored for TenderOps from platform/erp/backend/app/modules/cost_match/matcher.py.
# The ERP original resolves these through app.modules.cost_match.messages (a
# JSON MessageBundle); TenderOps ships no i18n layer, so the same keys are
# served from this inline table — reason codes are what the pipeline records.
DEFAULT_LOCALE = "bg"
_MESSAGES = {
    "match.hint.empty_query": "Въведете описание на материал или работа за търсене в себестойната база.",
    "match.hint.no_candidates": "Няма заредени позиции в локалната себестойна база.",
    "match.hint.no_good_match": "Няма уверен match. Опитайте с по-малко думи, само името на материала, или описание на друг език.",
    "match.reason.candidate_extra_spec": "Предложената позиция е по-конкретна от реда — изисква преглед.",
    "match.reason.exact_match": "Точно съвпадение по нормализирано описание.",
    "match.reason.partial_overlap": "Част от търсените термини са намерени в позицията.",
    "match.reason.spec_conflict": "Декларираните спецификации (диаметър, клас) са в конфликт — намалява увереността.",
    "match.reason.strong_overlap": "Всички търсени термини са намерени в позицията.",
    "match.reason.unit_match": "Мерките са съвместими.",
    "match.reason.unit_mismatch": "Мерките се различават — намалява увереността.",
    "match.reason.unit_unknown": "Мерката не може да бъде сравнена.",
    "match.reason.weak_overlap": "Малко или никакви търсени термини не съвпадат.",
    "match.reason.action_match": "Действието съвпада (монтаж/демонтаж/ремонт).",
    "match.reason.action_conflict": "Различно семейство действия — намалява увереността.",
    "match.reason.spec_match": "Декларирана спецификация съвпада.",
    "match.reason.no_content_overlap": "Няма общо съдържание.",
    "match.reason.thin_content_coverage": "Само едно общо съществително без потвърждение — под прага за преглед.",
}


def translate(key: str, locale: str = DEFAULT_LOCALE, **params: object) -> str:
    return _MESSAGES.get(key, key)


def is_key_present(key: str, locale: str = DEFAULT_LOCALE) -> bool:
    return key in _MESSAGES

# ── Confidence bands ────────────────────────────────────────────────────────

HIGH_CONFIDENCE = 0.75
"""At or above this score a match is shown as confident (green)."""

REVIEW_CONFIDENCE = 0.30
"""Between this and :data:`HIGH_CONFIDENCE` a match is flagged needs-review.

The floor sits where a suggestion still carries a real lexical overlap with the
line - a related item a person can accept, reject, or correct. Below it the
best candidate is still recorded and shown in the review queue for context,
but the tier says the base holds no equivalent worth deliberating over."""

_UNIT_MISMATCH_PENALTY = 0.55
"""Multiplier applied when query and candidate units differ in dimension."""

_COVERAGE_WEIGHT = 0.65
_OVERLAP_WEIGHT = 0.35

# Concept tokens that describe *what kind of work* the row is, not what it
# is made of. When a suggested catalogue row carries one of these and the
# bill line does not, the suggestion is a different job (renovation where
# the line is new work, demolition where it is installation) and must not
# read as a confident match - see ``candidate_extra_spec`` in score_match.
_WORK_MODIFIERS = frozenset({"demolition", "repair"})

_ACTION_MISMATCH_PENALTY = 0.5
"""Multiplier when the bill line and the catalogue row belong to different
action families - demolition priced as installation, or the reverse."""

# Concepts that name *which job* the row is. The concept table turns each
# surface verb ("демонтаж", "полагане", "ремонт") into one of these tokens,
# so the family check reads the same canonical sets the overlap score uses.
# Delivery/haulage/cleaning stay out: nearly every line says "доставка", so
# those concepts carry no work-type signal worth penalising on.
_ACTION_CONCEPTS = frozenset({"demolition", "installation", "repair"})

# Process concepts describe *what is done* to the item, not *what it is*.
# Nearly every Bulgarian bill line starts "доставка и монтаж на", so letting
# these tokens count towards coverage lets a shared prefix bridge unrelated
# objects - a patch panel priced as lintel panels, a power block priced as a
# comms cabinet. They still feed ``action_factor`` separately; exclusion
# affects only the content overlap. ``demolition`` and ``repair`` stay in
# content on purpose: generic action-family corpus rows ("Демонтаж",
# "Ремонт") are legitimate rates and remain reachable only through it.
_PROCESS_CONCEPTS = frozenset(
    {"delivery", "installation", "cleaning", "haulage"}
)

# Generic mass nouns carried by almost every corpus row ("работа",
# "система", "комплект", "труд и материали"). Stored as the post-stem forms
# ``canonical_tokens`` produces. They name no product, so a line whose only
# shared words are these has no evidence of equivalence.
_GENERIC_TERMS = frozenset(
    {"работ", "систем", "услуг", "комплект", "материал", "труд", "оборуд", "елемент",
     # A bare "панел" names no product - patch panels, lintel panels and
     # ceiling panels are different things sharing only a shape. The word
     # may still corroborate a match; it may never carry one alone.
     "панел"}
)

_CONTENT_COVERAGE_FLOOR = 0.4
"""Retained for reporting; the evidence formula no longer dilutes on it."""

# Evidence-driven confidence. Shared *content* is what proves a match -
# words the line spends on context (where the work happens, why, which
# standard) cannot subtract what they never contained. A line that names
# the product and the action deserves near-full trust even inside a long
# sentence; a line sharing one bare noun deserves suspicion.
_MATCH_BASE = 0.40
"""One corroborated content token already means a real family hit."""

_MATCH_PER_TOKEN = 0.30
"""The second shared content token - "полагане" + "гранит(огрес)" - lands
the pair in the confident band, the way an estimator reads it."""

_MATCH_EXTRA_TOKEN = 0.05
"""Diminishing returns past two tokens; extra agreement still counts."""

_ACTION_BONUS = 0.15
"""The bill and the row agree on which job this is (монтаж, демонтаж)."""

_SPEC_BONUS = 0.10
"""A declared spec class agreeing on a value corroborates the pairing."""


# ── Scope intent ────────────────────────────────────────────────────────────
# The corpus mixes price kinds a bill line must not confuse: bare materials,
# labour rates, machine days and composite work rates. A line's verb prefix
# declares which kind it is asking for ("Доставка на X" -> material,
# "Доставка и монтаж на X" -> work). The candidate's own scope travels in
# ``payload["scope"]``; the service applies a soft penalty on mismatch.
SCOPE_MATERIAL = "material"
SCOPE_WORK = "work"
SCOPE_ANY = "any"

# Concepts whose presence means the line prices a job, not a supply.
# delivery/haulage alone mean material supply; installation/demolition/
# repair/cleaning mean labour-inclusive work.
_WORK_INTENT_CONCEPTS = frozenset(
    {"installation", "demolition", "repair", "cleaning"}
)


def query_scope_intent(text: str | None) -> str:
    """Which price kind a bill line is asking for.

    ``"material"`` for supply-only lines ("Доставка на керемиди"),
    ``"work"`` once any work action is declared ("Доставка и монтаж",
    "Полагане на", "Демонтаж"), and ``"any"`` when the line carries no verb
    signal at all - an actionless line must not be penalised into a scope it
    never stated.
    """
    concepts = set(canonical_tokens(text)) & (_PROCESS_CONCEPTS | _ACTION_CONCEPTS)
    if concepts & _WORK_INTENT_CONCEPTS:
        return SCOPE_WORK
    if "delivery" in concepts or "haulage" in concepts:
        return SCOPE_MATERIAL
    return SCOPE_ANY


# Candidate scopes that satisfy each query intent. "composite" and "unknown"
# rows stay compatible with everything: a composite rate honestly contains
# the material a supply line needs, and an unscoped row must not be excluded
# on a guess. Only the four price-kind walls are enforced.
_SCOPE_CONFLICTS = {
    SCOPE_MATERIAL: frozenset({"labor", "machine", "service"}),
    SCOPE_WORK: frozenset({"material"}),
}


def scopes_conflict(query_intent: str, candidate_scope: str | None) -> bool:
    """Whether a candidate's price kind contradicts the line's declared need.

    ``candidate_scope`` values come from the corpus metadata (``material``,
    ``labor``, ``machine``, ``service``, ``composite``, ``unknown``); an
    empty or unknown scope can never conflict.
    """
    if not candidate_scope or candidate_scope in ("unknown", "composite"):
        return False
    return candidate_scope in _SCOPE_CONFLICTS.get(query_intent, frozenset())


# ── Accent folding and normalisation ────────────────────────────────────────

# Characters that do not decompose under NFKD but have a well-known Latin
# fold. Kept explicit so German, Scandinavian and Slavic-Latin spellings all
# collapse to a shared form before comparison.
_FOLD_MAP = {
    "ß": "ss",
    "ẞ": "ss",
    "ø": "o",
    "Ø": "o",
    "đ": "d",
    "Đ": "d",
    "ł": "l",
    "Ł": "l",
    "æ": "ae",
    "Æ": "ae",
    "œ": "oe",
    "Œ": "oe",
    "þ": "th",
    "ð": "d",
}

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def fold_accents(text: str) -> str:
    """Strip diacritics and fold special letters to a plain-Latin base.

    ``"béton"`` becomes ``"beton"``, ``"Dämmung"`` becomes ``"dammung"`` and
    ``"straße"`` becomes ``"strasse"``. Non-Latin scripts (for example
    Cyrillic) are left intact so their own synonym forms still match.
    """
    if not text:
        return ""
    out = []
    for ch in text:
        if ch in _FOLD_MAP:
            out.append(_FOLD_MAP[ch])
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        out.append(stripped or ch)
    return "".join(out)


def normalize_text(text: str | None) -> str:
    """Lower-case, fold accents and collapse whitespace to a canonical form.

    Punctuation and regex metacharacters are treated as plain separators, so
    input such as ``"C30/37 [*+]"`` is handled safely and never compiled as a
    pattern. Dimension numbers are glued to a single Latin-unit token, so a
    bill's ``"d = 5см"`` and a catalogue's ``"5 см"`` / ``"5cm"`` produce the
    same token ``5cm`` - specification digits only help when written
    differently on the two sides.
    """
    if not text:
        return ""
    folded = fold_accents(text).lower()
    # Spec-fold passes, symmetric on both sides: ``5х25``/``5x25``/``5×25``
    # unify on Latin ``x``, and a run of 3+ letters glued to digits splits
    # (``СВТ3`` -> ``свт 3``) while short spec markers keep glued
    # (``Ф110``, ``PN10``, ``C30``).
    folded = _X_SEP_RE.sub("x", folded)
    folded = _ALNUM_SPLIT_RE.sub(" ", folded)
    folded = _DIM_RE.sub(_dim_token, folded)
    tokens = _TOKEN_RE.findall(folded)
    return " ".join(tokens)


# ``x``/``х``/``×`` between digits is the same cross-section separator.
_X_SEP_RE = re.compile(r"(?<=\d)[xх×](?=\d)")
# A letter-run of 3+ characters glued to a digit is a word boundary the
# writer omitted ("СВТ3", "PVC40"); short glued markers ("Ф110", "PN10",
# "C30") are spec tokens and stay whole.
_ALNUM_SPLIT_RE = re.compile(r"(?<=[^\W\d_]{3})(?=\d)")


# ``50 мм``, ``5см``, ``5 cm`` -> ``50mm`` / ``5cm`` / ``5cm``. Cyrillic unit
# letters fold onto the Latin canonical so spec tokens match across scripts.
_DIM_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(мм|cm|mm|см|м2|m2|м3|m3|м|m)(?![\w])",
    re.UNICODE,
)
_DIM_UNIT = {"мм": "mm", "mm": "mm", "cm": "cm", "см": "cm",
             "м2": "m2", "m2": "m2", "м3": "m3", "m3": "m3",
             "м": "m", "m": "m"}


def _dim_token(m: re.Match) -> str:
    return f"{m.group(1).replace(',', '.')}{_DIM_UNIT[m.group(2)]}"


# ── Specification conflicts ────────────────────────────────────────────────
#
# Digit-bearing spec markers are facts, not keywords: a bill line declaring
# ``Ф75`` must never price a catalogue row declaring ``Ф110``, no matter how
# close the surrounding words are. Specs are read from the normalised text,
# so glued (``ф75``) and spaced (``ф 75``) spellings meet on one value, and
# ``_DIM_RE`` has already folded ``5 см``/``5cm``/``5cm`` into ``5cm``.
#
# A conflict exists only when BOTH sides declare the same spec class and
# share no value - a row silent on diameter may still be the right generic
# price, and silence is never a conflict.

_SPEC_CONFLICT_PENALTY = 0.5
"""Multiplier when query and candidate declare the same spec class with
disjoint values. Halving a strong match drops it out of the confident band
into review - a declared ``Ф75`` vs ``Ф110`` is a different product, but the
candidate is still shown for context rather than discarded outright."""

_SPEC_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # diameter: ф75 / Ф 110 / DN63 / d=400 / d 200 / ø200 (folds to ``o200``,
    # so the Latin-o form only counts glued - ``o 200`` would be the
    # Portuguese article "o" before any number).
    ("diameter", re.compile(r"(?:\b(?:ф|dn|d)\s*=?\s*|\bo)(\d+(?:[.,]\d+)?)")),
    # pressure class: PN10 / pn 16
    ("pressure", re.compile(r"\bpn\s*(\d+(?:[.,]\d+)?)")),
    # pipe wall ratio: SDR11 / sdr 17
    ("sdr", re.compile(r"\bsdr\s*(\d+(?:[.,]\d+)?)")),
    # concrete class: EU ``C30/37`` or Bulgarian ``В20`` glued - the spaced
    # ``в 20`` form is not read, because bare ``в`` is also the Bulgarian
    # preposition "in".
    ("concrete", re.compile(r"\bc\s*(\d+)\s*/\s*(\d+)\b|\bв(\d+(?:[.,]\d+)?)")),
    # paired sizes: 17/17 (siphon), 50/60 (thread), 30/37 (concrete also
    # declares the pair via its own class - both views stay consistent).
    ("size", re.compile(r"\b(\d+)\s*/\s*(\d+)\b")),
    # electrical power: 60 kW / 7,5 кВт - a 130 kW unit is a different
    # product from a 60 kW one, not a spelling variant of it.
    ("power", re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:[kк]wт?|[mм]wт?|квт|мвт)\b")),
    # dims already glued by _DIM_RE to latin units: 50mm / 5cm / 0.10m
    ("dim_len", re.compile(r"(\d+(?:\.\d+)?)(mm|cm|m)\b")),
    ("dim_area", re.compile(r"(\d+(?:\.\d+)?)m2\b")),
    ("dim_vol", re.compile(r"(\d+(?:\.\d+)?)m3\b")),
)

_DIM_LEN_TO_M = {"mm": Decimal("0.001"), "cm": Decimal("0.01"), "m": Decimal("1")}


def _spec_value(raw: str) -> str | None:
    """Canonicalise one captured spec value so ``75`` meets ``75.0``."""
    try:
        return format(Decimal(raw.replace(",", ".")).normalize(), "f")
    except InvalidOperation:
        return None


def extract_specs(text: str | None) -> dict[str, set[str]]:
    """Return declared spec classes -> value sets for ``text``.

    Extraction runs on the folded, dim-glued form - the same fold
    :func:`normalize_text` applies, but before ``_TOKEN_RE`` splits the
    result, because a glued ``0.10m`` would otherwise tokenise into ``0``
    and ``10m`` and read as ten metres. Every class present maps to a
    non-empty set of canonical values.
    """
    specs: dict[str, set[str]] = {}
    if not text:
        return specs
    norm = _DIM_RE.sub(_dim_token, fold_accents(text).lower())
    if not norm.strip():
        return specs
    for cls, pattern in _SPEC_RES:
        for match in pattern.finditer(norm):
            groups = match.groups()
            if cls == "concrete":
                if groups[2] is not None:
                    value = f"v{groups[2]}"
                else:
                    value = f"c{groups[0]}/{groups[1]}"
            elif cls == "size":
                value = f"{_spec_value(groups[0])}/{_spec_value(groups[1])}"
            elif cls == "dim_len":
                try:
                    metres = Decimal(groups[0].replace(",", ".")) * _DIM_LEN_TO_M[groups[1]]
                except InvalidOperation:
                    continue
                value = format(metres.normalize(), "f")
            else:
                value = _spec_value(groups[0])
            if value is not None:
                specs.setdefault(cls, set()).add(value)
    return specs


def spec_conflicts(query: str | None, candidate: str | None) -> list[str]:
    """Return the spec classes where both sides declare disjoint values.

    ``["diameter"]`` means the bill line and the candidate both state a
    diameter and none of the stated values agree. Empty list means either no
    spec is declared or every shared class has at least one common value.
    """
    q_specs = extract_specs(query)
    c_specs = extract_specs(candidate)
    return sorted(
        cls
        for cls, q_vals in q_specs.items()
        if cls in c_specs and q_vals.isdisjoint(c_specs[cls])
    )


# ── Multilingual synonym index ──────────────────────────────────────────────

# Concept -> surface forms in en/de/fr/es/it/pt/ru (and common variants).
# Surface forms are normalised at build time, so accents and case here are
# only for readability. Add a language by appending its word to the concept.
_CONCEPT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "concrete": ("concrete", "beton", "hormigon", "calcestruzzo", "betao", "betong", "бетон"),
    "reinforcement": (
        "reinforcement",
        "reinforced",
        "rebar",
        "bewehrung",
        "bewehrt",
        "armierung",
        "armatura",
        "armato",
        "armata",
        "armadura",
        "armado",
        "armada",
        "ferraillage",
        "armature",
        "arme",
        "армированный",
        "арматура",
        "армиране",
        "армирана",
        "армиран",
        "армирани",
        "армирано",
        "армировка",
        "армировъчни",
        "армировъчна",
        "армировъчен",
        "арматурна",
        "refuerzo",
    ),
    "formwork": (
        "formwork",
        "shuttering",
        "schalung",
        "coffrage",
        "encofrado",
        "cassaforma",
        "casseforme",
        "cofragem",
        "опалубка",
        "кофраж",
        "кофражен",
    ),
    "masonry": (
        "masonry",
        "brickwork",
        "brick",
        "mauerwerk",
        "ziegel",
        "maconnerie",
        "ladrillo",
        "mattone",
        "muratura",
        "alvenaria",
        "кирпич",
        "кладка",
        "зидария",
        "тухла",
        "тухли",
        "тухлен",
        "тухлена",
        "тухлени",
        "газобетон",
        "блокчета",
    ),
    "plaster": (
        "plaster",
        "plastering",
        "render",
        "putz",
        "enduit",
        "revoco",
        "enlucido",
        "intonaco",
        "reboco",
        "штукатурка",
        "мазилка",
        "мазилки",
        "хастар",
        "хастарна",
        "хастарен",
        "хастарни",
    ),
    # Power-troweled / floated screed finish: the corpus writes
    # "пердашена замазка", bill lines write "шлайфана настилка" -
    # same operation, different vocabulary.
    "troweled": (
        "пердашена",
        "пердаш",
        "изпердашване",
        "шлайфана",
        "шлайфани",
        "шлайфована",
        "шлайфован",
    ),
    "insulation": (
        "insulation",
        "dammung",
        "isolation",
        "aislamiento",
        "isolamento",
        "isolante",
        "coibentazione",
        "isolamento",
        "утеплитель",
        "изоляция",
        "изолация",
        "топлоизолация",
        "изолационна",
    ),
    "painting": (
        "paint",
        "painting",
        "anstrich",
        "malerarbeiten",
        "peinture",
        "pintura",
        "pittura",
        "verniciatura",
        "покраска",
        "окраска",
        "краска",
        "боядисване",
        "боя",
        "латекс",
    ),
    "screed": ("screed", "estrich", "chape", "solera", "massetto", "стяжка", "замазка"),
    "excavation": (
        "excavation",
        "earthwork",
        "aushub",
        "erdarbeiten",
        "terrassement",
        "excavacion",
        "scavo",
        "escavacao",
        "выемка",
        "земляные",
        "изкоп",
        "изкопни",
        "земни",
    ),
    "waterproofing": (
        "waterproofing",
        "abdichtung",
        "etancheite",
        "impermeabilizacion",
        "impermeabilizzazione",
        "impermeabilizacao",
        "гидроизоляция",
        "хидроизолация",
        "хидроизолационна",
    ),
    "tiling": (
        "tiling",
        "tile",
        "tiles",
        "fliesen",
        "carrelage",
        "alicatado",
        "baldosa",
        "piastrelle",
        "azulejo",
        "плитка",
        "фаянс",
        "теракот",
        "теракота",
        "гранитогрес",
        "гранит",
        "гранитни",
        "гранитна",
        "гранитен",
        "керамика",
        "керамични",
        "керамична",
        "плочки",
    ),
    "steel": ("steel", "stahl", "acier", "acero", "acciaio", "aco", "сталь", "стомана"),
    "timber": ("timber", "wood", "holz", "bois", "madera", "legno", "madeira", "дерево", "древесина", "дърво", "дървен", "дървени"),
    "door": ("door", "tur", "porte", "puerta", "porta", "дверь", "врата", "врати"),
    "window": ("window", "fenster", "fenetre", "ventana", "finestra", "janela", "окно", "прозорец", "прозорци", "дограма"),
    "roof": ("roof", "roofing", "dach", "toiture", "cubierta", "tejado", "tetto", "copertura", "кровля", "крыша", "покрив", "покривна"),
    "drywall": ("drywall", "plasterboard", "gipskarton", "trockenbau", "cartongesso", "гипсокартон", "гипскартон", "гипсфазер"),
    "pipe": ("pipe", "piping", "rohr", "tuyau", "tuberia", "tubo", "tubazione", "труба", "тръба", "тръби", "тръбопровод"),
    "cable": ("cable", "wiring", "kabel", "cavo", "кабель", "проводка", "кабел", "кабели", "проводник"),
    "sand": ("sand", "sable", "arena", "sabbia", "areia", "песок", "пясък"),
    "aggregate": ("gravel", "aggregate", "kies", "gravier", "grava", "ghiaia", "гравий", "щебень", "чакъл", "отсевки", "трошен"),
    "cement": (
        "cement",
        "zement",
        "ciment",
        "cemento",
        "цемент",
        "цимент",
        "циментна",
        "циментов",
        "циментова",
        "циментни",
    ),
    "wall": ("wall", "wand", "mur", "pared", "muro", "parete", "parede", "стена", "стени", "зид"),
    "slab": ("slab", "floor", "boden", "decke", "platte", "dalle", "losa", "solaio", "soletta", "плита", "перекрытие", "плоча", "плочи", "плочници"),
    "waterstop": ("waterstop", "fugenband", "гидрошпонка"),
    "membrane": ("membrane", "membran", "membrana", "мембрана"),
    "glazing": ("glazing", "glass", "glas", "verre", "vidrio", "vetro", "стекло", "стъкло", "остъкляване"),
    # ── Bulgarian country pack ────────────────────────────────────────────
    # Whole-word concepts Bulgarian bills use that the pan-European list
    # above never needed. Russian forms stay on their own concepts; these
    # are Bulgarian surface forms plus the shared EN/DE anchors so a bg
    # line also reaches a German or English base row.
    "demolition": (
        "demolition",
        "abbruch",
        "demolicion",
        "снос",
        "демонтаж",
        "демонтиране",
        "демонтажа",
        "разрушаване",
        "разрушавене",
        "разбиване",
        "разбивка",
        "съборяване",
        "събаряне",
        "куриране",
        "разваляне",
        "развалянето",
        "къртене",
        "къртенето",
        "разкъртване",
        "очукване",
        "изрязване",
        "сваляне",
        "свалянето",
        "снемане",
        "разкрепване",
    ),
    "backfill": (
        "backfill",
        "fill",
        "verfullung",
        "relleno",
        "засыпка",
        "насип",
        "насипни",
        "засипване",
        "запълване",
    ),
    "haulage": (
        "haulage",
        "transport",
        "abtransport",
        "транспорт",
        "извозване",
        "депониране",
        "отпадъци",
        "отпадък",
        "изхвърляне",
        "сметоизвозване",
        "натоварване",
        "разтоварване",
    ),
    "foundation": (
        "foundation",
        "fundament",
        "fondation",
        "cimentacion",
        "фундамент",
        "основа",
        "основи",
    ),
    "beam": ("beam", "balken", "viga", "trave", "poutre", "балка", "греда", "греди"),
    "building": (
        "building",
        "gebaude",
        "edificio",
        "batiment",
        "здание",
        "сграда",
        "сгради",
        "сградна",
    ),
    "column": (
        "column",
        "stutze",
        "columna",
        "colonna",
        "poteau",
        "колона",
        "колони",
        "стълб",
        "колонни",
    ),
    "ceiling": ("ceiling", "decke", "techo", "plafond", "soffitto", "таван", "тавани", "таванна"),
    "flooring": (
        "flooring",
        "bodenbelag",
        "suelo",
        "revetement",
        "pavimento",
        "настилка",
        "настилки",
        "паваж",
        "павета",
    ),
    "primer": (
        "primer",
        "grundierung",
        "imprimacion",
        "грунд",
        "грундиране",
        "грундиращ",
    ),
    "mortar": ("mortar", "mortel", "mortero", "malta", "разтвор", "малтер"),
    "foil": ("foil", "folie", "film", "фолио", "найлон"),
    "polyethylene": (
        "polyethylene",
        "polythene",
        "полиетилен",
        "полиетиленово",
        "полиетиленова",
        "полиетиленени",
    ),
    "adhesive": ("adhesive", "kleber", "colle", "лепило", "лепяща"),
    "curb": ("curb", "kerb", "bordstein", "бордюр", "бордюри"),
    "fence": ("fence", "zaun", "valla", "cloture", "ограда", "огради", "ограден"),
    "staircase": (
        "stairs",
        "staircase",
        "treppe",
        "escalera",
        "scala",
        "стълба",
        "стълби",
        "стълбище",
        "стъпала",
        "стъпало",
        "стъпени",
        "стъпе",
    ),
    "asphalt": ("asphalt", "asfalt", "асфалт", "асфалтова", "асфалтобетон"),
    "joint": ("joint", "fuge", "junta", "фуга", "фугиране", "фугиращ"),
    "sealant": (
        "sealant",
        "silikon",
        "dichtmasse",
        "sellador",
        "силикон",
        "уплътнение",
        "уплътнител",
    ),
    "scaffold": (
        "scaffold",
        "scaffolding",
        "gerust",
        "andamio",
        "echafaudage",
        "скеле",
    ),
    "putty": ("putty", "spachtel", "masilla", "шпакловка", "кит"),
    "repair": (
        "repair",
        "renovation",
        "ремонт",
        "ремонтна",
        "обновяване",
        "reparatur",
        "instandsetzung",
        "renovierung",
        "sanierung",
        "reparacion",
        "renovacion",
        "подмяна",
        "изкърпване",
        "пренареждане",
        "корекция",
        "коригиране",
    ),
    "gypsum_fiber": ("gipsfaser", "феромакел", "фиброгипс"),
    "hpl": ("hpl", "компактплоча"),
    # Delivery/installation verbs Bulgarian bills abbreviate relentlessly
    # ("Дост.и монтаж врата..."). Surface forms stay whole words; the
    # abbreviated spellings ride on the same concept.
    "delivery": (
        "delivery",
        "lieferung",
        "livraison",
        "entrega",
        "доставка",
        "доставката",
        "дост",
        "dostavka",
    ),
    "installation": (
        "installation",
        "montage",
        "montaj",
        "монтаж",
        "монт",
        "монтиране",
        "полагане",
        "направа",
        "изработка",
        "изпълнение",
        "иззиждане",
        "зиждане",
        "зидане",
        "поставяне",
        "сглобяване",
        "изграждане",
        "лепене",
        "дюбелиране",
        "забиване",
        "обръщане",
        "нивелиране",
        "изравняване",
        "подравняване",
        "укрепване",
        "шлайфане",
        "ваксане",
        "прокарване",
        "напасване",
        "оформяне",
        "огъване",
        "обработка",
        "редене",
        "нареждане",
    ),
    "cleaning": ("cleaning", "reinigung", "limpieza", "почистване", "почистването", "чистене"),
    "excavator": ("excavator", "bagger", "excavadora", "багер", "екскаватор"),
    "concrete_mixer": ("mixer", "mischer", "hormigonera", "бетонобъркачка"),
    "sauna": ("sauna", "сауна"),
    "profile": ("profile", "profil", "perfil", "профил", "профили"),
    "rail": ("rail", "schiene", "riel", "релса", "парапет", "ограждане"),
    # ── MEP / fit-out clusters seen on real Bulgarian bills ────────────────
    "fan": ("fan", "ventilator", "вентилатор", "вентилаторен", "вентилаторна"),
    "pump": ("pump", "pumpe", "pompa", "bomba", "помпа", "помпи", "помпена"),
    "reducer": (
        "reducer",
        "reduktion",
        "намалител",
        "преход",
        "редукция",
    ),
    "duct": (
        "duct",
        "kanal",
        "въздуховод",
        "въздуховоди",
        "канален",
        "спиро",
        "spiro",
        "лампен",
        "лампени",
    ),
    "recuperator": (
        "recuperator",
        "рекуператор",
        "рекуперативен",
        "рекуперативна",
    ),
    "nozzle": ("nozzle", "duse", "дюза", "дюзи", "шапка", "зонт"),
    "fridge": ("refrigerator", "fridge", "kuhlschrank", "хладилник", "хладилна"),
    "box": ("box", "kasten", "caja", "кутия", "кутии", "шкаф"),
    "cap": ("cap", "kappe", "deckel", "капак", "капаци"),
    "plastic": (
        "plastic",
        "kunststoff",
        "пластмаса",
        "пластмасов",
        "пластмасова",
        "пвц",
        "pvc",
    ),
    "cladding": (
        "cladding",
        "verkleidung",
        "облицовка",
        "облицоване",
        "обшивка",
        "обшиване",
    ),
    "elbow": ("elbow", "knie", "bogen", "коляно", "колена"),
    "grate": ("grate", "gitter", "roste", "решетка", "решетки", "рампа"),
    "bag": ("bag", "sack", "чувал", "чували"),
    "elastic": ("elastic", "elastisch", "еластична", "еластичен", "еластично"),
    "valve": ("valve", "ventil", "вентил", "клапа", "клапан", "кран"),
    "sensor": ("sensor", "датчик", "сензор"),
    "grounding": (
        "earthing",
        "grounding",
        "erdung",
        "заземителен",
        "заземяване",
        "заземление",
    ),
    "irrigation": ("irrigation", "bewasserung", "поливна", "апс"),
    "vinyl": ("vinyl", "винил", "винилова"),
    "filter": ("filter", "филтър", "филтри"),
    "metal_structure": ("металоконструкция", "металоконструкции"),
    "blinds": ("blinds", "jalusien", "жалузи", "щори"),
    "drainage": ("drainage", "drenazh", "дренаж", "дренажна", "дрениране"),
    "angle_bead": ("винкел", "винкли", "winkel", "ъгъл"),
}

# Multilingual stopwords stripped before scoring so grammar glue words do not
# dilute the token overlap. Kept intentionally small and language-spanning.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "for",
        "with",
        "in",
        "on",
        "to",
        "per",
        "der",
        "die",
        "das",
        "und",
        "mit",
        "aus",
        "fur",
        "von",
        "im",
        "le",
        "la",
        "les",
        "de",
        "du",
        "des",
        "et",
        "avec",
        "pour",
        "en",
        "el",
        "los",
        "las",
        "y",
        "con",
        "para",
        "il",
        "lo",
        "gli",
        "e",
        "di",
        "da",
        "o",
        "os",
        "as",
        "com",
        "и",
        "с",
        "из",
        "для",
        "на",
        # Bulgarian function words - carry no trade meaning yet otherwise
        # outscore real content words ("за", "по" matched every wall row
        # before the head noun even entered the candidate pool).
        "за",
        "по",
        "от",
        "в",
        "във",
        "върху",
        "над",
        "под",
        "до",
        "при",
        "чрез",
        "със",
        "без",
        "или",
        "като",
        "след",
        "преди",
        "около",
        "между",
        "включително",
        "вкл",
        "общо",
        "др",
        "други",
        "различни",
        "съществуващ",
        "съществуващи",
        "съществуваща",
        "съществуващо",
        "нов",
        "нова",
        "нови",
        "вид",
        "част",
        "поз",
        "позиция",
        "смр",
    }
)

# Reverse index: normalised surface form -> concept token. Built once.
_SYNONYM_INDEX: dict[str, str] = {}
for _concept, _forms in _CONCEPT_SYNONYMS.items():
    _SYNONYM_INDEX[_concept] = _concept
    for _form in _forms:
        _SYNONYM_INDEX[normalize_text(_form)] = _concept
del _concept, _forms, _form  # keep module namespace clean

# Surface forms long enough to be safely recognised inside a compound word
# (>= 4 chars), used only for closed compounds like German "Stahlbetonwand"
# where several concepts are glued into one token with no separator.
_COMPOUND_FORMS: tuple[tuple[str, str], ...] = tuple(
    (form, concept) for form, concept in _SYNONYM_INDEX.items() if len(form) >= 4
)

# Only tokens at least this long are scanned for glued concepts, so ordinary
# short words never trip a spurious substring hit.
_COMPOUND_MIN_LEN = 8


def _decompose_compound(token: str) -> list[str]:
    """Return concept tokens glued inside a long compound, or ``[]``.

    German (and Cyrillic) closed compounds such as ``"stahlbetonwand"`` or
    ``"железобетонная"`` carry several concepts in one token. We scan the
    curated multi-character surface forms and return every concept that
    appears as a substring, so the compound still matches its parts.
    """
    if len(token) < _COMPOUND_MIN_LEN:
        return []
    found: dict[str, None] = {}
    for form, concept in _COMPOUND_FORMS:
        if form in token:
            found.setdefault(concept, None)
    return list(found)


def canonical_tokens(text: str | None) -> tuple[str, ...]:
    """Return meaning-bearing tokens for ``text`` in a language-neutral form.

    Each word is folded, lower-cased, mapped through the multilingual synonym
    index to a shared concept when known, and stopwords are dropped. Long
    closed compounds are split into their concept parts. The result is
    order-preserving and de-duplicated so scoring is stable.
    """
    seen: dict[str, None] = {}
    for token in normalize_text(text).split():
        if token in _STOPWORDS:
            continue
        canonical = _SYNONYM_INDEX.get(token)
        if canonical is not None:
            seen.setdefault(canonical, None)
            continue
        parts = _decompose_compound(token)
        if parts:
            for part in parts:
                seen.setdefault(part, None)
        else:
            seen.setdefault(_stem(token), None)
    return tuple(seen)


# ── Light stemming ──────────────────────────────────────────────────────────
# Bulgarian inflects heavily: зидария / зидарията / зидарии, колона / колони,
# плоча / плочи / плочници. The suffix ladder below strips the commonest
# nominal/adjectival endings once, so inflected surface forms meet on a stem.
# It only runs on tokens the synonym index did not claim, so curated concepts
# (стълба -> staircase, not "column") win over the crude stem. Longest suffix
# first keeps -ване/-ание noun stems intact before single-letter endings.
_STEM_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "ование", "уване", "яване", "аване", "ание", "ение", "ване",
            "ане", "ене", "яне", "ция", "ията", "ката", "ата", "ите",
            "ове", "еве", "ост", "ия", "та", "те", "ят", "ът", "ет",
            "на", "ни", "но", "ен", "а", "е", "о", "и", "й",
        ),
        key=len,
        reverse=True,
    )
)
_STEM_MIN = 4  # stems shorter than this merge unrelated words


def _stem(token: str) -> str:
    """Strip one inflectional ending; keep the token if nothing applies."""
    for suffix in _STEM_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _STEM_MIN:
            return token[: -len(suffix)]
    return token


# ── Unit normalisation (metric + imperial) ──────────────────────────────────

# Normalised unit surface form -> physical dimension. Anything not listed is
# treated as unknown, which is neutral for scoring (never a false penalty).
_UNIT_DIMENSION: dict[str, str] = {
    # length
    "mm": "length",
    "cm": "length",
    "dm": "length",
    "m": "length",
    "km": "length",
    "lm": "length",
    "lfm": "length",
    "rm": "length",
    "in": "length",
    "inch": "length",
    "ft": "length",
    "foot": "length",
    "feet": "length",
    "yd": "length",
    "yard": "length",
    "mi": "length",
    "mile": "length",
    "мм": "length",
    "см": "length",
    "м": "length",
    "км": "length",
    "дм": "length",
    "мл": "length",
    "пм": "length",
    "линм": "length",
    "погм": "length",
    # area
    "m2": "area",
    "sqm": "area",
    "qm": "area",
    "quadratmeter": "area",
    "ha": "area",
    "are": "area",
    "sf": "area",
    "sqft": "area",
    "ft2": "area",
    "yd2": "area",
    "sqyd": "area",
    "м2": "area",
    "квм": "area",
    "дм2": "area",
    "ха": "area",
    "дка": "area",
    # volume
    "m3": "volume",
    "cbm": "volume",
    "kubikmeter": "volume",
    "cum": "volume",
    "cf": "volume",
    "cuft": "volume",
    "ft3": "volume",
    "yd3": "volume",
    "cuyd": "volume",
    "м3": "volume",
    "кубм": "volume",
    "дм3": "volume",
    "l": "volume",
    "л": "volume",
    "liter": "volume",
    "litre": "volume",
    "gal": "volume",
    "gallon": "volume",
    # mass
    "kg": "mass",
    "g": "mass",
    "mg": "mass",
    "t": "mass",
    "to": "mass",
    "ton": "mass",
    "tonne": "mass",
    "lb": "mass",
    "lbs": "mass",
    "pound": "mass",
    "oz": "mass",
    "cwt": "mass",
    "т": "mass",
    "тн": "mass",
    "тон": "mass",
    "кг": "mass",
    # count
    "pcs": "count",
    "pc": "count",
    "pce": "count",
    "piece": "count",
    "stk": "count",
    "stuck": "count",
    "st": "count",
    "ea": "count",
    "each": "count",
    "nr": "count",
    "no": "count",
    "un": "count",
    "u": "count",
    "pz": "count",
    "stuk": "count",
    "шт": "count",
    "бр": "count",
    "брой": "count",
    "компл": "count",
    "комплект": "count",
    "комплектен": "count",
    "полукомплект": "count",
    "броя": "count",
    "кт": "count",
    "ккт": "count",
    "чифт": "count",
    "пара": "count",
    # Bulgarian per-occurrence units from the catalogues: a row priced per
    # възел/станция/шкаф prices one occurrence, which is exactly what a
    # bill line measured in бр counts. Only count-dimension bill units can
    # ever take these rates - they never reach a per-м² line.
    "единица": "count",
    "възел": "count",
    "система": "count",
    "инсталация": "count",
    "устройство": "count",
    "стойка": "count",
    "опора": "count",
    "станция": "count",
    "камера": "count",
    "шкаф": "count",
    "асансьор": "count",
    "секция": "count",
    "кабина": "count",
    "блок": "count",
    "кутия": "count",
    "точка": "count",
    "място": "count",
    "елемент": "count",
    "комплекс": "count",
    "свързване": "count",
    "връзка": "count",
    "преход": "count",
    "проверка": "count",
    "измерване": "count",
    "снимка": "count",
    "рез": "count",
    "резка": "count",
    "лента": "count",
    "пръстен": "count",
    "канал": "count",
    "кабел": "count",
    "група": "count",
    "помещение": "count",
    "стая": "count",
    "етаж": "count",
    "стълб": "count",
    "част": "count",
    "ролка": "count",
    "калъф": "count",
    "бобина": "count",
    "пале": "count",
    "дупка": "count",
    "отвор": "count",
    "прът": "count",
    "ред": "count",
    "линия": "count",
    "завой": "count",
    "шахта": "count",
    "дърво": "count",
    "разклонение": "count",
    "кръстовище": "count",
    "настройка": "count",
    "имот": "count",
    "размах": "count",
    "наклон": "count",
    "мрежа": "count",
    "статив": "count",
    "конструкция": "count",
    "тракт": "count",
    "спирка": "count",
    "антена": "count",
    "носител": "count",
    "край": "count",
    "метър": "length",
    "кубометър": "volume",
    # plural forms of the per-occurrence nouns - "100 дупки", "10 връзки"
    "възела": "count",
    "възли": "count",
    "системи": "count",
    "инсталации": "count",
    "устройства": "count",
    "стойки": "count",
    "опори": "count",
    "станции": "count",
    "камери": "count",
    "шкафове": "count",
    "асансьори": "count",
    "секции": "count",
    "кабини": "count",
    "блокове": "count",
    "кутии": "count",
    "точки": "count",
    "места": "count",
    "елементи": "count",
    "комплекси": "count",
    "свързвания": "count",
    "връзки": "count",
    "преходи": "count",
    "проверки": "count",
    "измервания": "count",
    "снимки": "count",
    "резове": "count",
    "резки": "count",
    "ленти": "count",
    "пръстени": "count",
    "канали": "count",
    "кабели": "count",
    "групи": "count",
    "помещения": "count",
    "стаи": "count",
    "етажи": "count",
    "стълбове": "count",
    "части": "count",
    "ролки": "count",
    "калъфи": "count",
    "бобини": "count",
    "палета": "count",
    "дупки": "count",
    "отвори": "count",
    "пръта": "count",
    "пръти": "count",
    "редове": "count",
    "линии": "count",
    "завои": "count",
    "шахти": "count",
    "дървета": "count",
    "разклонения": "count",
    "кръстовища": "count",
    "настройки": "count",
    "имоти": "count",
    "размахове": "count",
    "наклони": "count",
    "мрежи": "count",
    "тативи": "count",
    "конструкции": "count",
    "трактове": "count",
    "спирки": "count",
    "антени": "count",
    "носители": "count",
    "краища": "count",
    "края": "count",
    "чифтове": "count",
    "пари": "count",
    "двойка": "count",
    "двойки": "count",
    # time / labour
    "h": "time",
    "hr": "time",
    "hour": "time",
    "std": "time",
    "stunde": "time",
    "day": "time",
    "tag": "time",
    "jour": "time",
    "ч": "time",
    "час": "time",
    "чч": "time",
    "машиночас": "time",
    # shift-length is deliberately NOT pinned (like "day"): a смяна is a
    # time-dimension unit, compatible with hours for recall, but no honest
    # hour-rate conversion is written without a stated shift length.
    "смяна": "time",
    "машиносмяна": "time",
    "мсм": "time",
    "време": "time",
    # lump sum
    "ls": "sum",
    "lumpsum": "sum",
    "lsum": "sum",
    "psch": "sum",
    "pauschal": "sum",
    "forfait": "sum",
    "global": "sum",
    "строеж": "sum",
    "обект": "sum",
}


_BULK_UNIT_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([^\d\s].*)$")

# Magnitude of one priced unit inside its dimension, expressed in the
# dimension's base unit (m, m2, m3, kg, piece, hour). Entries not listed here
# are still dimension-comparable through ``_UNIT_DIMENSION`` but carry no
# honest rate conversion - "day" of labour is not pinned to 8 hours, and a
# lump sum has no per-unit size at all.
_UNIT_MAGNITUDE: dict[str, Decimal] = {
    # length, base metre
    "mm": Decimal("0.001"), "мм": Decimal("0.001"),
    "cm": Decimal("0.01"), "см": Decimal("0.01"),
    "dm": Decimal("0.1"),
    "m": Decimal(1), "lm": Decimal(1), "lfm": Decimal(1), "rm": Decimal(1),
    "м": Decimal(1), "мл": Decimal(1), "пм": Decimal(1), "линм": Decimal(1),
    "погм": Decimal(1), "дм": Decimal("0.1"),
    "km": Decimal("1000"), "км": Decimal("1000"),
    "in": Decimal("0.0254"), "inch": Decimal("0.0254"),
    "ft": Decimal("0.3048"), "foot": Decimal("0.3048"), "feet": Decimal("0.3048"),
    "yd": Decimal("0.9144"), "yard": Decimal("0.9144"),
    "mi": Decimal("1609.344"), "mile": Decimal("1609.344"),
    # area, base m2
    "m2": Decimal(1), "sqm": Decimal(1), "qm": Decimal(1),
    "quadratmeter": Decimal(1), "м2": Decimal(1), "квм": Decimal(1),
    "are": Decimal("100"), "дка": Decimal("1000"), "дм2": Decimal("0.01"),
    "ha": Decimal("10000"), "ха": Decimal("10000"),
    "sf": Decimal("0.09290304"), "sqft": Decimal("0.09290304"),
    "ft2": Decimal("0.09290304"), "yd2": Decimal("0.83612736"), "sqyd": Decimal("0.83612736"),
    # volume, base m3
    "m3": Decimal(1), "cbm": Decimal(1), "kubikmeter": Decimal(1), "cum": Decimal(1),
    "м3": Decimal(1), "кубм": Decimal(1), "дм3": Decimal("0.001"),
    "l": Decimal("0.001"), "л": Decimal("0.001"), "liter": Decimal("0.001"), "litre": Decimal("0.001"),
    "gal": Decimal("0.003785411784"), "gallon": Decimal("0.003785411784"),
    "cf": Decimal("0.028316846592"), "cuft": Decimal("0.028316846592"), "ft3": Decimal("0.028316846592"),
    "yd3": Decimal("0.764554857984"), "cuyd": Decimal("0.764554857984"),
    # mass, base kg
    "mg": Decimal("0.000001"),
    "g": Decimal("0.001"),
    "kg": Decimal(1), "кг": Decimal(1),
    "t": Decimal("1000"), "to": Decimal("1000"), "ton": Decimal("1000"),
    "tonne": Decimal("1000"), "т": Decimal("1000"), "тн": Decimal("1000"), "тон": Decimal("1000"),
    "lb": Decimal("0.45359237"), "lbs": Decimal("0.45359237"), "pound": Decimal("0.45359237"),
    "oz": Decimal("0.028349523125"),
    "cwt": Decimal("45.359237"),
    # count, base piece
    "pcs": Decimal(1), "pc": Decimal(1), "pce": Decimal(1), "piece": Decimal(1),
    "stk": Decimal(1), "stuck": Decimal(1), "st": Decimal(1), "ea": Decimal(1),
    "each": Decimal(1), "nr": Decimal(1), "no": Decimal(1), "un": Decimal(1),
    "u": Decimal(1), "pz": Decimal(1), "stuk": Decimal(1),
    "шт": Decimal(1), "бр": Decimal(1), "брой": Decimal(1), "компл": Decimal(1),
    "комплект": Decimal(1), "броя": Decimal(1),
    # Every Bulgarian per-occurrence noun maps 1:1 onto the piece - the
    # priced unit IS one възел/станция/чифт, same as one бр.
    "комплектен": Decimal(1), "полукомплект": Decimal(1), "кт": Decimal(1),
    "ккт": Decimal(1), "чифт": Decimal(1), "пара": Decimal(1),
    "единица": Decimal(1), "възел": Decimal(1), "система": Decimal(1),
    "инсталация": Decimal(1), "устройство": Decimal(1), "стойка": Decimal(1),
    "опора": Decimal(1), "станция": Decimal(1), "камера": Decimal(1),
    "шкаф": Decimal(1), "асансьор": Decimal(1), "секция": Decimal(1),
    "кабина": Decimal(1), "блок": Decimal(1), "кутия": Decimal(1),
    "точка": Decimal(1), "място": Decimal(1), "елемент": Decimal(1),
    "комплекс": Decimal(1), "свързване": Decimal(1), "връзка": Decimal(1),
    "преход": Decimal(1), "проверка": Decimal(1), "измерване": Decimal(1),
    "снимка": Decimal(1), "рез": Decimal(1), "резка": Decimal(1),
    "лента": Decimal(1), "пръстен": Decimal(1), "канал": Decimal(1),
    "кабел": Decimal(1), "група": Decimal(1), "помещение": Decimal(1),
    "стая": Decimal(1), "етаж": Decimal(1), "стълб": Decimal(1),
    "част": Decimal(1), "ролка": Decimal(1), "калъф": Decimal(1),
    "бобина": Decimal(1), "пале": Decimal(1), "дупка": Decimal(1),
    "отвор": Decimal(1), "прът": Decimal(1), "ред": Decimal(1),
    "линия": Decimal(1), "завой": Decimal(1), "шахта": Decimal(1),
    "дърво": Decimal(1), "разклонение": Decimal(1), "кръстовище": Decimal(1),
    # time, base hour - only the same-shape units convert; "day" stays out
    "h": Decimal(1), "hr": Decimal(1), "hour": Decimal(1), "std": Decimal(1), "stunde": Decimal(1),
    "ч": Decimal(1), "час": Decimal(1), "чч": Decimal(1), "машиночас": Decimal(1),
    # lump sum, base whole - one bill line, one corpus rate
    "ls": Decimal(1), "lumpsum": Decimal(1), "lsum": Decimal(1),
    "psch": Decimal(1), "pauschal": Decimal(1), "forfait": Decimal(1),
    "global": Decimal(1), "строеж": Decimal(1), "обект": Decimal(1),
}


# Every count-dimension unit is per-occurrence: one възел prices exactly
# like one бр, so magnitude defaults to 1 for any count key not explicitly
# pinned above.
for _u, _d in list(_UNIT_DIMENSION.items()):
    if _d == "count" and _u not in _UNIT_MAGNITUDE:
        _UNIT_MAGNITUDE[_u] = Decimal(1)


def _fold_unit_key(text: str) -> str:
    """The canonical lookup form of a unit word.

    Table keys and input both pass through this so ``fold_accents`` quirks
    (``й`` decomposes to ``и`` under NFKD) can never split the two sides:
    ``"Брой"`` folds to ``"брои"`` and so does the ``"брой"`` table key.
    """
    folded = fold_accents(text).lower().replace("²", "2").replace("³", "3")
    return re.sub(r"[\s.\-/'′\"]", "", folded)


# Both tables are declared in readable spelling above; the lookup dicts are
# keyed on the folded form so keys and input meet on identical footing.
_UNIT_DIMENSION = {_fold_unit_key(k): v for k, v in _UNIT_DIMENSION.items()}
_UNIT_MAGNITUDE = {_fold_unit_key(k): v for k, v in _UNIT_MAGNITUDE.items()}


def _unit_norm(unit: str | None) -> tuple[str, Decimal, tuple[str, ...]] | None:
    """``(key, bulk_multiplier, words)`` of a unit token, or None when empty.

    ``key`` is the whole-token fold used for direct table lookup; ``words``
    keeps the unglued tokens so qualified units like ``"км тръби"`` can
    resolve their leading physical unit when the whole string misses.
    """
    if not unit:
        return None
    folded = fold_accents(unit).lower().replace("²", "2").replace("³", "3").strip()
    if not folded:
        return None
    multiplier = Decimal(1)
    bulk = _BULK_UNIT_RE.match(folded)
    if bulk:
        try:
            multiplier = Decimal(bulk.group(1).replace(",", "."))
        except InvalidOperation:
            multiplier = Decimal(1)
        folded = bulk.group(2).strip()
    key = re.sub(r"[\s.\-/'′\"]", "", folded)
    words = tuple(re.findall(r"\w+", folded))
    return key, multiplier, words


def _resolve_unit_key(key: str, words: tuple[str, ...]) -> str | None:
    """The table key a parsed unit resolves to, or None when unknown.

    Whole-token first; when that misses, the first word gets a chance -
    catalogue units qualified by context (``"км тръби"``, ``"м сечение"``,
    ``"m3 строителен обе"``) lead with the physical unit. Only the first
    word is trusted: suffix qualifiers like ``"канал.км"`` would invert the
    reading, so they stay unknown.
    """
    if key in _UNIT_DIMENSION:
        return key
    if len(words) > 1:
        if words[0] in _UNIT_DIMENSION:
            return words[0]
        # Trailing qualifier form - "10 свързващи точки" prices per point.
        # Restricted to count nouns so "комплект 10 м" can never be read
        # as a per-metre price for a per-set row.
        if words[-1] in _UNIT_DIMENSION and _UNIT_DIMENSION[words[-1]] == "count":
            return words[-1]
    return None


def _unit_key(unit: str | None) -> tuple[str, Decimal] | None:
    """``(dimension, magnitude)`` of a unit, bulk prefix folded in, or None.

    ``"100 м3"`` resolves to ``("volume", 100)`` - one priced unit is a
    hundred cubic metres - and ``"10 броя"`` to ``("count", 10)``.
    """
    parsed = _unit_norm(unit)
    if parsed is None:
        return None
    key, multiplier, words = parsed
    resolved = _resolve_unit_key(key, words)
    if resolved is None:
        return None
    magnitude = _UNIT_MAGNITUDE.get(resolved)
    if magnitude is None:
        return None
    return _UNIT_DIMENSION[resolved], magnitude * multiplier


def unit_scale(unit: str | None) -> Decimal:
    """The leading quantity multiplier on a priced unit, ``1`` when absent.

    Catalogue rows priced per ``"100 м3"`` or ``"10 броя"`` carry a rate for
    the whole hundred or dozen, not for a single unit. Kept for callers that
    only need the bulk prefix; :func:`unit_rate_factor` is the full
    conversion between two units.
    """
    if not unit:
        return Decimal(1)
    m = _BULK_UNIT_RE.match(unit.strip())
    if not m:
        return Decimal(1)
    try:
        return Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return Decimal(1)


def unit_rate_factor(from_unit: str | None, to_unit: str | None) -> Decimal | None:
    """Multiply a per-``from_unit`` price by this to get a per-``to_unit`` price.

    ``rate_factor("100 т", "кг")`` is ``1/100_000`` - one priced unit is a
    hundred tonnes, a hundred thousand kilos - so a tonne-scale catalogue
    rate lands honestly on a per-kg bill line. Identical units factor to 1
    even when the unit has no pinned magnitude ("day" vs "day"); ``None`` is
    returned when the units spell differently and no honest conversion
    exists - unknown unit, or different dimensions. The caller treats
    ``None`` as "no honest conversion", never as "factor one".
    """
    if not from_unit or not to_unit:
        return None
    src_norm = _unit_norm(from_unit)
    dst_norm = _unit_norm(to_unit)
    if src_norm is not None and src_norm == dst_norm:
        return Decimal(1)
    src = _unit_key(from_unit)
    dst = _unit_key(to_unit)
    if src is None or dst is None or src[0] != dst[0]:
        return None
    return dst[1] / src[1]


def normalize_unit(unit: str | None) -> str | None:
    """Return the physical dimension of a unit, or ``None`` if unknown.

    Superscripts and separators are folded so ``"m²"``, ``"m2"``, ``"sq m"``
    and ``"SQM"`` all resolve to ``"area"``, and imperial units land on the
    same dimension as their metric counterparts. A leading quantity
    multiplier is dropped first: ``"100 м3"`` is a volume price, the
    hundred only scales the rate (see :func:`unit_scale`).
    """
    if not unit:
        return None
    parsed = _unit_norm(unit)
    if parsed is None:
        return None
    key, _multiplier, words = parsed
    resolved = _resolve_unit_key(key, words)
    return _UNIT_DIMENSION.get(resolved) if resolved else None


def units_compatible(a: str | None, b: str | None) -> bool | None:
    """Compare two units by dimension.

    Returns ``True`` if both map to the same dimension, ``False`` if they map
    to different dimensions, and ``None`` when either unit is unknown so the
    caller can treat it as no-signal rather than a mismatch.
    """
    dim_a = normalize_unit(a)
    dim_b = normalize_unit(b)
    if dim_a is None or dim_b is None:
        return None
    return dim_a == dim_b


# ── Data carriers ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    """A single cost-database entry to score against a query.

    ``payload`` carries opaque pass-through data (for example a Decimal
    unit rate and currency) that the matcher never mutates or coerces, so
    money stays Decimal-exact end to end.
    """

    ref: str
    text: str
    unit: str | None = None
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class MatchScore:
    """The confidence of one query-candidate comparison, fully explained."""

    confidence: float
    band: str  # "high" | "medium" | "low"
    factors: dict[str, float]
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchResult:
    """Outcome of matching a query against a set of candidates."""

    query: str
    candidate: Candidate | None
    score: MatchScore | None
    is_confident: bool
    tie: bool
    hint: str | None
    alternatives: list[tuple[Candidate, MatchScore]] = field(default_factory=list)


def _tokens_equivalent(a: str, b: str) -> bool:
    """Two canonical tokens carry the same fact.

    Equal stems match outright; otherwise a prefix relation of at least
    five characters counts, so Bulgarian compounds and adjectival stems
    the suffix ladder cannot fully reduce still meet - ``гранит`` reaches
    ``гранитогрес``/``гранитн``-style leftovers, ``армир`` reaches
    ``армировъч``. Below five characters a prefix is too generic to trust.
    """
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 5 and hi.startswith(lo)


def _shared_tokens(q_cmp: set[str], c_cmp: set[str]) -> tuple[set[str], set[str]]:
    """The token pairs that actually match, morphological variants included.

    Returns ``(query-side matches, candidate-side matches)`` - both views,
    because one corpus token can answer several query words and the
    candidate's own coverage is the tiebreak view.
    """
    q_shared = {
        qt for qt in q_cmp if any(_tokens_equivalent(qt, ct) for ct in c_cmp)
    }
    c_shared = {
        ct for ct in c_cmp if any(_tokens_equivalent(qt, ct) for qt in q_cmp)
    }
    return q_shared, c_shared


def _band(confidence: float) -> str:
    """Map a raw confidence to a traffic-light band."""
    if confidence >= HIGH_CONFIDENCE:
        return "high"
    if confidence >= REVIEW_CONFIDENCE:
        return "medium"
    return "low"


def score_match(
    query: str,
    candidate_text: str,
    *,
    query_unit: str | None = None,
    candidate_unit: str | None = None,
) -> MatchScore:
    """Score how well ``candidate_text`` answers ``query`` in ``[0, 1]``.

    The returned :class:`MatchScore` exposes every factor and a list of
    reason codes so the number is auditable. See the module docstring for
    the exact formula.
    """
    q_tokens = canonical_tokens(query)
    c_tokens = canonical_tokens(candidate_text)
    factors: dict[str, float] = {
        "query_coverage": 0.0,
        "term_overlap": 0.0,
        "unit_factor": 1.0,
        "action_factor": 1.0,
        "exact": 0.0,
    }
    reasons: list[str] = []

    # Unit relationship first: it can only reduce a score, never inflate it.
    compat = units_compatible(query_unit, candidate_unit)
    if compat is True:
        reasons.append("unit_match")
    elif compat is False:
        factors["unit_factor"] = _UNIT_MISMATCH_PENALTY
        reasons.append("unit_mismatch")
    elif query_unit or candidate_unit:
        reasons.append("unit_unknown")

    if not q_tokens or not c_tokens:
        reasons.append("weak_overlap")
        return MatchScore(confidence=0.0, band="low", factors=factors, reasons=reasons)

    # Normalised exact equality short-circuits to a perfect content score.
    if normalize_text(query) == normalize_text(candidate_text):
        factors["exact"] = 1.0
        factors["query_coverage"] = 1.0
        factors["term_overlap"] = 1.0
        confidence = round(1.0 * factors["unit_factor"] * factors["action_factor"], 4)
        reasons.insert(0, "exact_match")
        return MatchScore(confidence=confidence, band=_band(confidence), factors=factors, reasons=reasons)

    q_set = set(q_tokens)
    c_set = set(c_tokens)

    # Action family: "Демонтаж на теракот" and "направа на облицовка фаянс"
    # share the tiles but describe opposite jobs. When both sides declare a
    # family and the families disagree, the candidate is a different kind of
    # work - it must not read as confident whatever the nouns say.
    q_actions = q_set & _ACTION_CONCEPTS
    c_actions = c_set & _ACTION_CONCEPTS

    # Coverage and overlap run on content tokens only: process concepts and
    # generic mass nouns are stripped so "Доставка и монтаж на X" cannot ride
    # its prefix into a match with "Доставка и монтаж на Y". Degenerate
    # sides (all process words) fall back to the full set so a query like
    # "демонтаж" alone still finds the generic demolition rows. A declared
    # action the other side does not share is counted as unexplained query
    # content - it widens the denominator instead of bridging the numerator.
    q_content = q_set - _PROCESS_CONCEPTS - _GENERIC_TERMS
    c_content = c_set - _PROCESS_CONCEPTS - _GENERIC_TERMS
    q_cmp = q_content or q_set
    c_cmp = c_content or c_set
    q_shared, c_shared = _shared_tokens(q_cmp, c_cmp)
    inter = len(q_shared)
    # Bare digits are not content evidence: "20 програми" on a washing
    # machine and "-20 °C" on a chiller share nothing real, yet two such
    # hits would otherwise reach the strong-overlap floor. Meaningful
    # numbers arrive either as spec classes (Ф110, 3 мм) - compared via
    # extract_specs - or as mixed tokens (C12/15, 17/17, ф2) which stay
    # in evidence. Pure digits may still add corroboration through
    # ``inter`` once at least one real word is shared.
    q_evidence = {tok for tok in q_shared if not tok.isdigit()}
    q_denom = len(q_cmp) + len(q_actions - c_actions)
    c_denom = len(c_cmp) + len(c_actions - q_actions)
    coverage = inter / q_denom
    # Dice coefficient: symmetrical size penalty both ways - a one-word
    # candidate ("Розетки") can no longer claim full overlap against a long
    # line, and a verbose catalogue row pays for its extra spec words.
    overlap = 2 * inter / (q_denom + c_denom)
    factors["query_coverage"] = round(coverage, 4)
    factors["term_overlap"] = round(overlap, 4)
    factors["matched_tokens"] = float(inter)
    # How much of the *candidate* the line explains - the tiebreak view.
    # When two rows share the same evidence, the one the line covers
    # completely is the simpler, better answer.
    factors["candidate_coverage"] = round(len(c_shared) / max(1, len(c_cmp)), 4)
    if q_actions and c_actions:
        if q_actions & c_actions:
            reasons.append("action_match")
        else:
            factors["action_factor"] = _ACTION_MISMATCH_PENALTY
            reasons.append("action_conflict")

    # Declared-spec conflict: Ф75 in the bill against Ф110 in the catalogue
    # row is a different product regardless of how similar the prose is.
    # The same extracted specs carry the positive signal: when both sides
    # declare a class with a common value, the agreement is evidence worth
    # marking on the result (no score change - corroboration, not proof).
    q_specs = extract_specs(query)
    c_specs = extract_specs(candidate_text)
    conflicts = [
        cls
        for cls, q_vals in q_specs.items()
        if cls in c_specs and q_vals.isdisjoint(c_specs[cls])
    ]
    spec_conflict = bool(conflicts)
    spec_agree = not spec_conflict and any(cls in c_specs for cls in q_specs)

    # Evidence-driven confidence: what the two sides actually share carries
    # the score. Words the line says that no corpus row shares (стълби is
    # where the granite goes, not what it is) explain nothing either way -
    # they dilute nothing. One shared noun alone stays suspicious: "пач
    # панел" priced as надлеглови панели is a bridge, not evidence, unless
    # an action family or a declared spec corroborates it.
    shared_action = bool(q_actions & c_actions)
    if q_content:
        if inter == 0:
            base = 0.0
        else:
            base = _MATCH_BASE + _MATCH_PER_TOKEN * min(inter, 2) + _MATCH_EXTRA_TOKEN * max(0, inter - 2)
            if shared_action:
                base += _ACTION_BONUS
            if spec_agree:
                base += _SPEC_BONUS
            base = min(1.0, base)
    else:
        # Degenerate line (all process words): keep the old coverage view -
        # there is no content evidence to weigh.
        base = _COVERAGE_WEIGHT * coverage + _OVERLAP_WEIGHT * overlap
    confidence = round(base * factors["unit_factor"] * factors["action_factor"], 4)

    if spec_conflict:
        factors["spec_conflict"] = 1.0
        confidence = round(confidence * _SPEC_CONFLICT_PENALTY, 4)
        reasons.append("spec_conflict")
    elif spec_agree:
        factors["spec_match"] = 1.0
        reasons.append("spec_match")

    # Candidate-side divergence: extra tokens in a catalogue row are normal
    # verbosity ("... (труд и теракол)"), but when the extras include a
    # work-type modifier the line never stated - renovation instead of new
    # work, demolition instead of installation - the row describes a
    # different job. That is a spec mismatch, the exact thing the review
    # queue exists for, so it must not read as a confident match.
    if q_set < c_set and (c_set - q_set) & _WORK_MODIFIERS:
        factors["candidate_work_modifier"] = 1.0
        confidence = round(min(confidence, HIGH_CONFIDENCE - 0.01), 4)
        reasons.append("candidate_extra_spec")

    # Content floor: a suggestion needs shared content tokens, and a
    # single-noun bridge with nothing else corroborating it stays buried.
    # A match that ran on process words alone - the washing machine offered
    # for staff training - is no evidence at all. One borrowed noun ("пач
    # панел" priced as надлеглови панели) is not either. But a single
    # content noun backed by the action family or a declared spec (полагане
    # + гранитогрес for "Полагане на гранит-стълби") is real evidence -
    # missing words explain nothing, they never subtract.
    if q_content:
        if not q_evidence:
            confidence = 0.0
            reasons.append("no_content_overlap")
        elif len(q_evidence) <= 1 and not shared_action and not spec_agree:
            confidence = round(min(confidence, REVIEW_CONFIDENCE - 0.01), 4)
            reasons.append("thin_content_coverage")

    if coverage >= 0.99:
        reasons.insert(0, "strong_overlap")
    elif inter > 0:
        reasons.insert(0, "partial_overlap")
    else:
        reasons.insert(0, "weak_overlap")

    return MatchScore(confidence=confidence, band=_band(confidence), factors=factors, reasons=reasons)


def explain(score: MatchScore, locale: str = DEFAULT_LOCALE) -> str:
    """Render a match's reason codes as a localized, human-readable sentence."""
    parts = [translate(f"match.reason.{code}", locale=locale) for code in score.reasons]
    return " ".join(parts)


def no_match_hint(reason: str, locale: str = DEFAULT_LOCALE) -> str:
    """Return a short, plain-language hint for a non-result.

    ``reason`` is one of ``"empty_query"``, ``"no_candidates"`` or
    ``"no_good_match"``.
    """
    return translate(f"match.hint.{reason}", locale=locale)


def best_match(
    query: str | None,
    candidates: Iterable[Candidate] | Sequence[Candidate],
    *,
    query_unit: str | None = None,
    locale: str = DEFAULT_LOCALE,
    top_n: int = 3,
) -> MatchResult:
    """Find the best cost-database candidate for ``query``, with guards.

    Handles the awkward cases explicitly so callers never have to: an empty
    or whitespace-only query and an empty candidate set both return a result
    with ``candidate=None`` and a plain-language ``hint``. Ties on the top
    score are resolved by input order and flagged via ``tie=True``. When the
    best score is below :data:`REVIEW_CONFIDENCE` the candidate is still
    offered as ``candidate`` for context but ``is_confident`` is ``False`` and
    a hint suggests what to try next.
    """
    normalized_query = (query or "").strip()
    if not normalized_query or not canonical_tokens(normalized_query):
        return MatchResult(
            query=normalized_query,
            candidate=None,
            score=None,
            is_confident=False,
            tie=False,
            hint=no_match_hint("empty_query", locale=locale),
        )

    scored: list[tuple[Candidate, MatchScore]] = []
    for cand in candidates:
        score = score_match(
            normalized_query,
            cand.text,
            query_unit=query_unit,
            candidate_unit=cand.unit,
        )
        scored.append((cand, score))

    if not scored:
        return MatchResult(
            query=normalized_query,
            candidate=None,
            score=None,
            is_confident=False,
            tie=False,
            hint=no_match_hint("no_candidates", locale=locale),
        )

    # Stable sort: highest confidence first; equal confidence goes to the
    # candidate the line explains most completely (the simpler answer),
    # then to more shared tokens, then input order.
    ordered = sorted(
        enumerate(scored),
        key=lambda pair: (
            -pair[1][1].confidence,
            -pair[1][1].factors.get("candidate_coverage", 0.0),
            -pair[1][1].factors.get("matched_tokens", 0.0),
            pair[0],
        ),
    )
    top_index, (best_cand, best_score) = ordered[0]
    tie = len(ordered) > 1 and ordered[1][1][1].confidence == best_score.confidence

    alternatives = [(cand, score) for _, (cand, score) in ordered[:top_n]]
    is_confident = best_score.confidence >= HIGH_CONFIDENCE
    hint = None
    if best_score.confidence < REVIEW_CONFIDENCE:
        hint = no_match_hint("no_good_match", locale=locale)

    return MatchResult(
        query=normalized_query,
        candidate=best_cand,
        score=best_score,
        is_confident=is_confident,
        tie=tie,
        hint=hint,
        alternatives=alternatives,
    )


def suggestion_rate(candidate: Candidate) -> Decimal | None:
    """Extract the candidate's unit rate as an exact Decimal, if present.

    Reads ``payload["unit_rate"]`` without float coercion so currency values
    stay Decimal-exact. Strings are parsed through Decimal; anything missing
    or unparseable yields ``None`` rather than a lossy fallback.
    """
    if not candidate.payload:
        return None
    raw = candidate.payload.get("unit_rate")
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, str):
        try:
            return Decimal(raw)
        except (ValueError, ArithmeticError):
            return None
    return None


__all__ = [
    "HIGH_CONFIDENCE",
    "REVIEW_CONFIDENCE",
    "Candidate",
    "MatchResult",
    "MatchScore",
    "best_match",
    "canonical_tokens",
    "explain",
    "fold_accents",
    "no_match_hint",
    "normalize_text",
    "normalize_unit",
    "unit_scale",
    "score_match",
    "suggestion_rate",
    "units_compatible",
]
