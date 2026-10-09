"""Versioned Bulgarian work definitions shared by bills and original quotations."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WorkDefinition:
    id: str
    object_patterns: tuple[str, ...]
    notation: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class CanonicalWork:
    definition_id: str | None
    object: str | None
    operations: tuple[str, ...]
    scope: str | None
    materials: tuple[str, ...]
    specs: dict[str, tuple[str, ...]]
    inclusions: tuple[str, ...]
    exclusions: tuple[str, ...]
    locations: tuple[str, ...]
    residual_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    path = Path(__file__).with_name('work_definitions_bg.json')
    data: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
    if data['version'] != 1:
        raise ValueError('Unsupported Bulgarian work-definition version')
    return data


@lru_cache(maxsize=1)
def definitions() -> dict[str, WorkDefinition]:
    data = catalog()
    return {entry['id']: WorkDefinition(entry['id'], tuple(entry['object_patterns']),
                                         tuple(entry['notation'])) for entry in data['families']}


def extra_object_patterns() -> tuple[tuple[str, str], ...]:
    return tuple((entry.id, pattern) for entry in definitions().values()
                 for pattern in entry.object_patterns)


def concept_patterns(section: str) -> tuple[tuple[str, str], ...]:
    return tuple((entry['id'], entry['pattern']) for entry in catalog()[section])


@lru_cache(maxsize=16384)
def notation(text: str, family: str | None) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], str]:
    """Return typed parameters and text with *only recognized* notation masked.

    Technical parameters remain in the signature; masking prevents two
    spellings of the same dimension from becoming unexplained lexical words.
    An unsupported model or unit is never masked or silently translated.
    """
    definition = definitions().get(family or '')
    values: dict[str, set[str]] = {}
    spans: list[tuple[int, int]] = []
    if definition is not None:
        for rule in definition.notation:
            canonicalize = rule.get('canonicalize') or []
            for match in re.finditer(rule['pattern'], text, re.I):
                spans.append(match.span())
                for name in rule['fields']:
                    raw = match.group(name).lower()
                    # Coordinated elements price as a set: "плоча, греди,
                    # пояси и плоча" is {slab, beam, belt}, not one value.
                    # The conjunction must be a standalone word - "ивични"
                    # carries the letter "и" inside it.
                    parts = re.split(r'\s*,\s*|\s+и\s+', raw) if name == 'element' else [raw]
                    for value in parts:
                        value = value.strip()
                        if not value:
                            continue
                        if name != 'model':
                            try:
                                value = format(Decimal(value.replace(',', '.')).normalize(), 'f')
                            except ArithmeticError:
                                pass
                        canonical = False
                        for pattern, replacement in canonicalize:
                            if re.fullmatch(pattern, value, re.I):
                                value = replacement
                                canonical = True
                                break
                        if not canonical and ' ' in value:
                            # adjective+noun compounds ("стълбищни площадки")
                            # take the noun's canonical element.
                            last = value.rsplit(None, 1)[-1]
                            for pattern, replacement in canonicalize:
                                if re.fullmatch(pattern, last, re.I):
                                    value = replacement
                                    break
                        values.setdefault(name, set()).add(value)
    remainder = text
    for start, end in sorted(spans, reverse=True):
        remainder = remainder[:start] + ' ' + remainder[end:]
    return tuple(sorted((name, tuple(sorted(v))) for name, v in values.items())), remainder


# Tokens that are spec notation, not vocabulary: a dimension, a rating, a
# diameter is already compared through the signature's typed spec fields.
# Whatever its spelling, it must never count as unexplained residual words.
_SPEC_TOKEN_RE = re.compile(
    r"^(?:"
    r"\d+(?:[.,]\d+)?"
    r"|[a-zа-я]"
    r"|(?:[oо]|ф|dn|м)\d+(?:[.,]\d+)?"
    r"(?:mm\d*|cm\d*|dm|мм\d*|см\d*|дм|m\d?|м\d?|kg|g|t|кг|г|т|kw|kva|mw|квт?|ква|мв|"
    r"w|вт|v|в|a|а|ma|ма|pa|па|mpa|мпа|l|л|hz|гц|db|дб|kn|кн|бр|s|h|с|ч|ден|д|год)?"
    r"|\d+(?:[.,]\d+)?(?:mm\d*|cm\d*|dm|m\d?|kg|g|t|kw|kva|mw|w|v|a|ma|pa|mpa|l|hz|db|kn|"
    r"мм\d*|см\d*|м\d?|кг|г|т|квт?|ква|мв|вт|в|а|ма|па|мпа|л|гц|дб|кн|м|бр|s|h|с|ч|ден|д|год|mm|cm|m)"
    r"|(?:mm|cm|dm|m|мм|см|дм|м|kg|кг|kw|квт?|ква|mpa|мпа|db|дб|kn|кн|бр|ghz?|гц)\d*"
    r"|[α-ω]"
    r"|\d+[xх×]\d+(?:[.,]\d+)?(?:[xх×]\d+(?:[.,]\d+)?)?(?:mm2?|cm|мм2?|см)?"
    r"|\d+/\d+"
    r"|[cvbсв]\d+(?:/\d+)?"
    r"|ф\s*\d+(?:[.,]\d+)?(?:mm|мм)?"
    r"|dn\d+|ip\d+|pn\d+|sdr\d+|sn\d+"
    r"|q\s*[а-яa-z]{1,3}\s*(?:[=:]\s*\d+(?:[.,]\d+)?)?"
    r")$", re.I)


@lru_cache(maxsize=16384)
def canonical_work(text: str) -> CanonicalWork:
    from app.modules.cost_match.bulgarian import parse_work
    from app.modules.cost_match.matcher import _GENERIC_TERMS, _PROCESS_CONCEPTS, _head_text, canonical_tokens

    parsed = parse_work(text)
    _, remainder = notation(text, parsed.object)
    definition = definitions().get(parsed.object or '')
    if definition is not None:
        patterns = list(definition.object_patterns)
        # Mask every concept word the vocabulary recognizes, parsed or
        # folded: "зариване" stays backfill vocabulary even when the ops
        # set normalizes it to fill.
        patterns.extend(pattern for name, pattern in concept_patterns('operations')
                        if name in parsed.operations
                        or (name == 'install' and 'reinstall' in parsed.operations)
                        or re.search(pattern, remainder))
        patterns.extend(pattern for name, pattern in concept_patterns('materials')
                        if name in parsed.materials or re.search(pattern, remainder))
        patterns.extend(rule['pattern'] for rule in catalog().get('context_operations', [])
                        if rule['id'] in parsed.operations and parsed.object in rule['objects'])
        from app.modules.cost_match.bulgarian import _LOCATION_RES
        patterns.extend(pattern.pattern for value, pattern in _LOCATION_RES
                        if value in parsed.locations)
        masked = {i for pattern in patterns for match in re.finditer(pattern, remainder, re.I)
                  for i in range(*match.span())}
        remainder = ''.join(' ' if i in masked else char for i, char in enumerate(remainder))
    residual = {t for t in canonical_tokens(_head_text(remainder))
                if t not in _PROCESS_CONCEPTS and t not in _GENERIC_TERMS
                and not _SPEC_TOKEN_RE.fullmatch(t)}
    return CanonicalWork(
        definition_id=parsed.object if parsed.object in definitions() else None,
        object=parsed.object,
        operations=tuple(sorted(parsed.operations)), scope=parsed.scope,
        materials=tuple(sorted(parsed.materials)),
        specs={key: tuple(sorted(values)) for key, values in parsed.specs},
        inclusions=tuple(sorted(parsed.tail)), exclusions=tuple(sorted(parsed.excluded)),
        locations=tuple(sorted(parsed.locations)),
        residual_terms=tuple(sorted(residual)),
    )


@lru_cache(maxsize=1)
def catalog_digest() -> str:
    encoded = json.dumps(catalog(), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return sha256(encoded.encode('utf-8')).hexdigest()


def work_metadata(text: str) -> dict[str, Any]:
    work = canonical_work(text).as_dict()
    encoded = json.dumps(work, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return {'schema_version': catalog()['version'], 'catalog_digest': catalog_digest(), 'work': work,
            'fingerprint': sha256(encoded.encode('utf-8')).hexdigest(),
            'interpretation_status': 'deterministic_unverified'}
