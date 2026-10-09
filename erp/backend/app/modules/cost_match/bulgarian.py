"""Deterministic Bulgarian work identity, independent of lexical confidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from app.modules.cost_match.work_catalog import catalog, concept_patterns, extra_object_patterns, notation

_BG = re.compile(r"[А-Яа-я]")
_HEADER = re.compile(
    r"^(?:смр\s*[-–:]?\s*разпределение\b|разпределение\s+кота\b|"
    r"(?:част|раздел|глава|подраздел)\s*[:\d]|(?:общо|всичко|междинна сума)\b|"
    r"строително[- ]монтажни работи\s*$)", re.I,
)
_TAIL = re.compile(r"\b(?:вкл\.?|включително|включва\w*|съгласно)\b", re.I)
_EXCLUDE = re.compile(r"\b(?:без|не\s+включва\w*)\b([^.;]*)", re.I)
_BOILERPLATE = frozenset(
    ["вкл", "включително", "включва", "съгласно", "всички", "свързани", "свързаните", "с", "със", "това", "разходи", "разходите", "изисквания", "изискванията", "на", "за", "и", "от", "тс", "техническата", "спецификация", "техническите", "спецификации", "крайна", "цена", "крайната", "цената"]
)
_OP_RES = tuple((value, re.compile(pattern, re.I)) for value, pattern in concept_patterns('operations'))

# Each noun phrase names a priced entity, not merely a related trade.
_OBJECT_RES = tuple((value, re.compile(pattern, re.I)) for value, pattern in extra_object_patterns())
_MATERIAL_RES = tuple((value, re.compile(pattern, re.I)) for value, pattern in concept_patterns('materials'))
_LOCATION_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Surface nouns count wherever they stand - "мазилка стени" puts the
    # wall in the same place as "мазилка по стени".
    ('wall', re.compile(r'\b(?:по|на|и)\s+(?:стен|зид)\w*|\b(?:стен|зид)\w*', re.I)),
    ('ceiling', re.compile(r'\b(?:по|на|и)\s+таван\w*|\bтаван\w*', re.I)),
    ('floor', re.compile(r'\b(?:по|на|и)\s+под(?:а|ове|овете)?\b|\bпод(?:а|ове|овете)?\b', re.I)),
    ('column', re.compile(r'\b(?:по|на|и)\s+колон\w*|\bколон\w*', re.I)),
    ('roof', re.compile(r'\b(?:по|на|и)\s+покрив\w*|\bпокрив\w*', re.I)),
    ('plinth', re.compile(r'\bцокъл\w*', re.I)),
    ('staircase', re.compile(r'\bстълб\w*', re.I)),
    ('basement', re.compile(r'\b(?:сутерен\w*|мазе\w*)', re.I)),
    ('facade', re.compile(r'\b(?:фасад\w*|външн\w*)', re.I)),
    ('interior', re.compile(r'\bвътрешн\w*', re.I)),
)

# Site operations bundled around the priced object - dig, refill, haul,
# drain, compact - are priced per object elsewhere, so a quotation silent
# about them is missing evidence, never a contradiction. Everything else
# is part of the priced verb bundle and a gap is a conflict.
ANCILLARY_OPS = frozenset({'excavate', 'backfill', 'haul', 'drain', 'fill', 'compact'})


def is_structural(text: str | None) -> bool:
    return bool(_HEADER.search((text or '').strip()))


def identity_text(text: str | None) -> str:
    from app.modules.cost_match.matcher import normalize_text

    norm = normalize_text(text)
    norm = re.sub(r"\s+краина цена\s*$", "", norm)
    marker = _TAIL.search(norm)
    if marker and set(norm[marker.start():].split()) <= _BOILERPLATE:
        norm = norm[:marker.start()].strip()
    return norm


@dataclass(frozen=True)
class WorkIdentity:
    object: str | None
    operations: frozenset[str]
    scope: str | None
    materials: frozenset[str]
    specs: tuple[tuple[str, frozenset[str]], ...]
    tail: frozenset[str]
    excluded: frozenset[str]
    locations: frozenset[str]


def _number(value: str) -> str:
    return format(Decimal(value.replace(',', '.')).normalize(), 'f')


@lru_cache(maxsize=32768)
def parse_work(text: str) -> WorkIdentity:
    from app.modules.cost_match.matcher import (
        _GENERIC_TERMS,
        _PROCESS_CONCEPTS,
        canonical_tokens,
        extract_specs,
        fold_accents,
    )

    norm = fold_accents(text.replace('∅', 'ф').replace('Ø', 'ф').replace('ø', 'ф')).lower()
    marker = _TAIL.search(norm)
    main = norm[:marker.start()] if marker else norm
    # Longest match at the earliest position wins: "водомерна шахта" is a
    # manhole, not a water meter - the compound names the priced entity.
    entities = [(m.start(), -(m.end() - m.start()), i, value)
                for i, (value, pattern) in enumerate(_OBJECT_RES)
                if (m := pattern.search(main))]
    obj = min(entities)[3] if entities else None
    exclusions = list(_EXCLUDE.finditer(norm))
    positive = norm
    for match in reversed(exclusions):
        positive = positive[:match.start()] + ' ' + positive[match.end():]
    operations = {value for value, pattern in _OP_RES if pattern.search(positive)}
    operations.update(rule['id'] for rule in catalog().get('context_operations', [])
                      if obj in rule['objects'] and re.search(rule['pattern'], positive, re.I))
    if 'install' in operations and re.search(r"\b(?:обратен|обратно|повторен|повторно)\b.{0,20}\bмонтаж", norm):
        operations.remove('install')
        operations.add('reinstall')
    # Backfill and plain fill price the same earthworks rate: "обратен
    # насип" and "насипване с пясък" are one billed activity.
    if 'backfill' in operations:
        operations.discard('backfill')
        operations.add('fill')
    supply = bool(re.search(r"\bдоставка\b", norm))
    no_supply = bool(re.search(r"\b(?:без\s+(?:доставка|материал\w*)|само\s+труд|труд\s+само)\b", norm))
    work = bool(operations)
    scope = 'labour' if no_supply else 'supply_install' if supply and work else 'material' if supply else None
    materials = {value for value, pattern in _MATERIAL_RES if pattern.search(main)}
    specs = extract_specs(text.replace('∅', 'ф').replace('Ø', 'ф').replace('ø', 'ф'))
    canonical_values, _ = notation(text, obj)
    if obj == 'cable' and {'cores', 'section'} <= dict(canonical_values).keys():
        # The generic pair extractor can recognize only one of 3x1,5mm²
        # and 3х1,5мм2. Its cable interpretation is replaced, never dropped
        # for a different physical dimension in the same row.
        layout = {f'{n}x{s}' for n in dict(canonical_values)['cores']
                  for s in dict(canonical_values)['section']}
        if specs.get('dim_pair') == layout:
            specs.pop('dim_pair')
    if obj == 'masonry' and specs.get('dim_pair'):
        # "1/2 тухла" reads as both a brick-unit notation and a bare size
        # pair. The fraction is the same fact - keep only the canonical one.
        brick = dict(canonical_values).get('brick_units', frozenset())
        fractions = {format(Decimal(a) / Decimal(b), 'f').rstrip('0').rstrip('.')
                     for pair in specs['dim_pair']
                     for a, b in [pair.split('x')]
                     if Decimal(b) and Decimal(a) <= 4 and Decimal(b) <= 4}
        fractions = {f for f in fractions if f} - {''}
        if fractions and fractions <= set(brick):
            specs.pop('dim_pair')
    for name, values in canonical_values:
        if name != 'diameter':
            specs.setdefault(f'cable_{name}', set()).update(values)
    # Inch punctuation is semantic. Backticks and doubled apostrophes are
    # common Bulgarian workbook spellings of the inch mark.
    inches = re.compile(r"(?<!\w)(?:[фФ∅Øø]|DN\s*)?\s*(\d+(?:[.,]\d+)?(?:\s+\d+/\d+|/\d+)?)\s*(?:\"|″|``|''|инч\w*)", re.I)
    for match in inches.finditer(text):
        value = match.group(1).replace(',', '.')
        if ' ' in value.strip():
            whole, fraction = value.split()
            numerator, denominator = fraction.split('/')
            if not Decimal(denominator):
                continue
            number = Decimal(whole) + Decimal(numerator) / Decimal(denominator)
        elif '/' in value:
            numerator, denominator = value.split('/')
            if not Decimal(denominator):
                continue
            number = Decimal(numerator) / Decimal(denominator)
        else:
            number = Decimal(value)
        specs.setdefault('inch_connection', set()).add(format(number.normalize(), 'f'))
    if specs.get('inch_connection') and specs.get('diameter'):
        # ``Ф2"`` is a two-inch pipe, never a 2 mm bore: the inch reading
        # owns every number that carries the mark.
        specs['diameter'] -= specs['inch_connection']
        if not specs['diameter']:
            del specs['diameter']
    for cls, pattern in (
        ('flow_m3h', r"(\d+(?:[.,]\d+)?)\s*[mм](?:3|³)\s*/\s*(?:h|ч)\b"),
        ('flow_m3d', r"(\d+(?:[.,]\d+)?)\s*[mм](?:3|³)\s*/\s*(?:d|ден|д)\b"),
        ('pressure_pa', r"(\d+(?:[.,]\d+)?)\s*pa\b"),
        ('wattage', r"(\d+(?:[.,]\d+)?)\s*(?:w|вт)\b"),
        ('voltage', r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:v|в)\b"),
        ('protection_ip', r"\bip\s*(\d+)\b"),
    ):
        for match in re.finditer(pattern, text, re.I):
            specs.setdefault(cls, set()).add(_number(match.group(1)))
    tail = set()
    if marker:
        tail_text = norm[marker.end():]
        if not set(identity_text(tail_text).split()) <= _BOILERPLATE:
            tail = set(canonical_tokens(tail_text)) - _PROCESS_CONCEPTS - _GENERIC_TERMS
    excluded = set().union(*(set(canonical_tokens(m.group(1))) - _PROCESS_CONCEPTS - _GENERIC_TERMS
                             for m in exclusions))
    locations = {value for value, pattern in _LOCATION_RES
             if re.search(pattern, main)}
    return WorkIdentity(obj, frozenset(operations), scope, frozenset(materials),
                        tuple(sorted((k, frozenset(v)) for k, v in specs.items())), frozenset(tail),
                        frozenset(excluded), frozenset(locations))


def compare_work(query: str, candidate: str) -> tuple[list[str], list[str]]:
    """Return contradictions and missing evidence separately; neither is identity."""
    q, c = parse_work(query), parse_work(candidate)
    conflicts: list[str] = []
    missing: list[str] = []
    if q.object and c.object and q.object != c.object:
        conflicts.append(f'work_object_conflict:{q.object}:{c.object}')
    elif not q.object or not c.object:
        missing.append('work_object_unknown')
    if q.operations != c.operations:
        if q.operations and c.operations:
            if not (q.operations <= c.operations or c.operations <= q.operations):
                conflicts.append('work_operation_bundle_conflict')
            else:
                extra = (q.operations | c.operations) - (q.operations & c.operations)
                if extra <= ANCILLARY_OPS or (extra == {'demolish'}
                                             and q.object in {'formwork', 'scaffold'}):
                    # Site work bundled around the object and the striking
                    # half of a scaffold/formwork cycle are missing evidence
                    # the pool can arbitrate - not contradictions.
                    missing.append('work_operation_unknown')
                else:
                    conflicts.append('work_operation_bundle_conflict')
        else:
            missing.append('work_operation_unknown')
    if q.scope and c.scope and q.scope != c.scope:
        conflicts.append(f'work_scope_conflict:{q.scope}:{c.scope}')
    elif q.scope != c.scope:
        missing.append('work_scope_unknown')
    if q.materials and c.materials and q.materials.isdisjoint(c.materials):
        conflicts.append('work_material_conflict')
    elif q.materials != c.materials:
        missing.append('work_material_incomplete')
    if q.locations and c.locations and q.locations.isdisjoint(c.locations):
        conflicts.append('work_location_conflict')
    elif q.locations != c.locations:
        missing.append('work_location_incomplete')
    q_specs, c_specs = dict(q.specs), dict(c.specs)
    # Elevation levels ("кота +3,60") locate the work, they do not price
    # it - a column formwork rate is the same at any level.
    q_specs.pop('level', None)
    c_specs.pop('level', None)
    for cls in q_specs.keys() | c_specs.keys():
        q_values, c_values = q_specs.get(cls, frozenset()), c_specs.get(cls, frozenset())
        if q_values and c_values and q_values.isdisjoint(c_values):
            conflicts.append(f'work_spec_conflict:{cls}')
        elif q_values != c_values:
            missing.append(f'work_spec_incomplete:{cls}')
    if q.tail != c.tail:
        missing.append('work_inclusions_incomplete')
    if q.excluded != c.excluded:
        missing.append('work_exclusions_incomplete')
    if q.excluded & c.tail or c.excluded & q.tail:
        conflicts.append('work_exclusions_conflict')
    return conflicts, missing


def is_bulgarian(text: str) -> bool:
    return bool(_BG.search(text))
