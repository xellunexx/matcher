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
            for match in re.finditer(rule['pattern'], text, re.I):
                spans.append(match.span())
                for name in rule['fields']:
                    value = match.group(name).lower()
                    if name != 'model':
                        value = format(Decimal(value.replace(',', '.')).normalize(), 'f')
                    values.setdefault(name, set()).add(value)
    remainder = text
    for start, end in sorted(spans, reverse=True):
        remainder = remainder[:start] + ' ' + remainder[end:]
    return tuple(sorted((name, tuple(sorted(v))) for name, v in values.items())), remainder


@lru_cache(maxsize=16384)
def canonical_work(text: str) -> CanonicalWork:
    from app.modules.cost_match.bulgarian import parse_work
    from app.modules.cost_match.matcher import _GENERIC_TERMS, _PROCESS_CONCEPTS, _head_text, canonical_tokens

    parsed = parse_work(text)
    _, remainder = notation(text, parsed.object)
    definition = definitions().get(parsed.object or '')
    if definition is not None:
        patterns = list(definition.object_patterns)
        patterns.extend(pattern for name, pattern in concept_patterns('operations')
                        if name in parsed.operations or name == 'install' and 'reinstall' in parsed.operations)
        patterns.extend(pattern for name, pattern in concept_patterns('materials')
                        if name in parsed.materials)
        patterns.extend(rule['pattern'] for rule in catalog().get('context_operations', [])
                        if rule['id'] in parsed.operations and parsed.object in rule['objects'])
        masked = {i for pattern in patterns for match in re.finditer(pattern, remainder, re.I)
                  for i in range(*match.span())}
        remainder = ''.join(' ' if i in masked else char for i, char in enumerate(remainder))
    residual = set(canonical_tokens(_head_text(remainder))) - _PROCESS_CONCEPTS - _GENERIC_TERMS
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
