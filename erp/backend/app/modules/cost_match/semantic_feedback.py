"""Reviewable Bulgarian work-vector bridges; never a price or eligibility override."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from app.modules.cost_match.work_catalog import canonical_work, catalog, catalog_digest, definitions

VERSION = 'bg-work-link/v1:'
_LABELS = {
    'action': 'Действие', 'object': 'Обект', 'material': 'Материал',
    'location': 'Място', 'substrate': 'Основа',
    'scope': 'Обхват и допълнителна работа',
    'inclusions': 'Включено', 'exclusions': 'Изключено',
    'details': 'Характеристики и размери',
}


def _normalized(text: str) -> str:
    return ' '.join(text.casefold().split())


def _expression(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group() if match else ''


def _operation(text: str, operations: tuple[str, ...]) -> str:
    for operation in operations:
        for item in catalog()['operations']:
            if item['id'] == operation:
                found = _expression(text, item['pattern'])
                if found:
                    return found
    return ''


def _object(text: str, definition_id: str | None) -> str:
    definition = definitions().get(definition_id or '')
    if definition is None:
        return ''
    for pattern in definition.object_patterns:
        found = _expression(text, pattern)
        if found:
            return found
    return ''


def _material(text: str, materials: tuple[str, ...]) -> str:
    for entry in catalog()['materials']:
        if entry['id'] in materials:
            found = _expression(text, entry['pattern'])
            if found:
                return found
    return ''


def _residual(text: str, terms: tuple[str, ...]) -> str:
    if not terms:
        return ''
    from app.modules.cost_match.matcher import canonical_tokens

    return ' '.join(
        token for token in re.findall(r'\w+', text, flags=re.UNICODE)
        if set(canonical_tokens(token)) & set(terms)
    )


def semantic_links(query: str, quotation: str) -> list[dict[str, str]]:
    """Pair source words by parsed role; absent/different roles stay visible."""
    q, c = canonical_work(query), canonical_work(quotation)
    pairs = [
        ('action', _operation(query, q.operations), _operation(quotation, c.operations),
         q.operations[:1], c.operations[:1]),
        ('object', _object(query, q.definition_id), _object(quotation, c.definition_id),
         q.object, c.object),
        ('material', _material(query, q.materials), _material(quotation, c.materials),
         q.materials, c.materials),
        ('substrate', _material(query, q.attributes.get('substrate_material', ())),
         _material(quotation, c.attributes.get('substrate_material', ())),
         q.attributes.get('substrate_material', ()), c.attributes.get('substrate_material', ())),
        ('location', _expression(query, r'\b(?:стен\w*|зид\w*|под\w*|таван\w*|покрив\w*)'),
         _expression(quotation, r'\b(?:стен\w*|зид\w*|под\w*|таван\w*|покрив\w*)'),
         q.locations, c.locations),
        ('scope', ' / '.join(filter(None, [_residual(query, q.residual_terms),
                                          _operation(query, q.operations[1:])])),
         ' / '.join(filter(None, [_residual(quotation, c.residual_terms),
                                  _operation(quotation, c.operations[1:])])),
         (q.scope, q.residual_terms, q.inclusions, q.exclusions, q.operations[1:]),
         (c.scope, c.residual_terms, c.inclusions, c.exclusions, c.operations[1:])),
        ('inclusions', _operation(query, q.inclusions), _operation(quotation, c.inclusions),
         q.inclusions, c.inclusions),
        ('exclusions', _expression(query, r'\bбез\s+\w+'),
         _expression(quotation, r'\bбез\s+\w+'), q.exclusions, c.exclusions),
        ('details', _expression(query, r'\b\d+(?:[.,]\d+)?\s*(?:мм|см|м)\b'),
         _expression(quotation, r'\b\d+(?:[.,]\d+)?\s*(?:мм|см|м)\b'),
         (q.specs, q.attributes, q.contexts), (c.specs, c.attributes, c.contexts)),
    ]
    links: list[dict[str, str]] = []
    for role, source, target, query_vector, target_vector in pairs:
        if not (source or target or query_vector or target_vector):
            continue
        encoded = json.dumps(
            [catalog_digest(), role, _normalized(query), _normalized(quotation), query_vector, target_vector],
            ensure_ascii=False, sort_keys=True, default=str,
        )
        links.append({
            'id': sha256(encoded.encode('utf-8')).hexdigest()[:24],
            'role': role, 'label': _LABELS[role],
            'query': source or '—', 'candidate': target or '—',
        })
    return links


def encode_link(link: dict[str, str], verdict: str, code: str, unit: str) -> str:
    return VERSION + json.dumps({
        'id': link['id'], 'role': link['role'], 'query': link['query'],
        'candidate': link['candidate'], 'verdict': verdict, 'code': code,
        'unit': unit,
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def decode_link(value: Any) -> dict[str, str] | None:
    if not isinstance(value, str) or not value.startswith(VERSION):
        return None
    try:
        decoded = json.loads(value[len(VERSION):])
    except (TypeError, ValueError):
        return None
    if (not isinstance(decoded, dict) or decoded.get('verdict') not in ('same', 'different')
            or any(not isinstance(decoded.get(key), str)
                   for key in ('id', 'role', 'query', 'candidate', 'code', 'unit'))
            or not re.fullmatch(r'[a-f0-9]{24}', decoded['id'])
            or decoded['role'] not in _LABELS):
        return None
    return decoded
