"""Versioned Bulgarian work definitions shared by bills and original quotations."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
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
    attributes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    contexts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    secondary_work: tuple[tuple[str, str], ...] = ()
    defaults_applied: tuple[str, ...] = ()
    concept_ids: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    path = Path(__file__).with_name('work_definitions_bg.json')
    data: dict[str, Any] = json.loads(path.read_text(encoding='utf-8'))
    if data['version'] not in (1, 2):
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


def concept_id(kind: str, name: str) -> int:
    value: int = catalog()['concept_ids'][kind][name]
    return value


def operator_pricing_rule(text: str) -> dict[str, Any] | None:
    work = canonical_work(text)
    for rule in catalog().get('operator_pricing_rules', []):
        if (work.object == rule['object']
                and work.operations == tuple(rule['operations'])
                and work.scope == rule['scope']
                and set(work.materials) == set(rule['materials'])
                and set(work.locations) == set(rule['locations'])
                and not (work.specs or work.inclusions or work.exclusions
                         or work.secondary_work or work.residual_terms)
                and all(key in rule['attributes'] and set(values) <= set(rule['attributes'][key])
                        for key, values in work.attributes.items())):
            return dict(rule)
    return None


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
    from app.modules.cost_match.bulgarian import parse_work, substrate_spans
    from app.modules.cost_match.matcher import _GENERIC_TERMS, _PROCESS_CONCEPTS, _head_text, canonical_tokens

    parsed = parse_work(text)
    materials = set(parsed.materials)
    remainder = text
    specs = {key: tuple(sorted(values)) for key, values in parsed.specs}
    attributes: dict[str, set[str]] = {}
    contexts: dict[str, tuple[str, ...]] = {}
    context_locations: set[str] = set()
    for start, end in substrate_spans(text, parsed.object):
        substrate = text[start:end]
        attributes.setdefault('substrate_material', set()).update(
            name for name, pattern in concept_patterns('materials')
            if re.search(pattern, substrate, re.I)
        )
        remainder = remainder[:start] + ' ' * (end - start) + remainder[end:]
    for family in catalog()['families']:
        if family['id'] != parsed.object:
            continue
        object_spans = {i for pattern in family['object_patterns']
                        for match in re.finditer(pattern, remainder, re.I)
                        for i in range(*match.span())}
        remainder = ''.join(' ' if i in object_spans else char for i, char in enumerate(remainder))
        for rule in family.get('material_equivalences', []):
            if any(materials == set(variant) for variant in rule['variants']):
                contexts['material_variant'] = tuple(sorted(materials))
                materials = set(rule['canonical'])
        for key in family.get('project_spec_fields', []):
            if key in specs:
                contexts['project:' + key] = specs.pop(key)
        for rule in family.get('attributes', []):
            if not set(rule.get('operations', ())) <= set(parsed.operations):
                continue
            for match in re.finditer(rule['pattern'], text, re.I):
                if 'capture' in rule:
                    name = match.group(rule['capture']).strip().lower()
                    value = rule.get('aliases', {}).get(name, name)
                    if rule.get('numeric'):
                        value = format(Decimal(value.replace(',', '.')).normalize(), 'f')
                else:
                    value = rule['value']
                if rule.get('role') == 'project':
                    key = 'project:' + rule['field']
                    contexts[key] = tuple(sorted(set(contexts.get(key, ())) | {value}))
                    if spec_field := rule.get('spec_field'):
                        remaining = set(specs.get(spec_field, ())) - {value}
                        if remaining:
                            specs[spec_field] = tuple(sorted(remaining))
                        else:
                            specs.pop(spec_field, None)
                else:
                    attributes.setdefault(rule['field'], set()).add(value)
                context_locations.update(rule.get('context_locations', ()))
                remainder = re.sub(rule['pattern'], ' ', remainder, flags=re.I)
    _, remainder = notation(remainder, parsed.object)
    if parsed.object == 'masonry' and attributes.get('wall_thickness_cm'):
        thickness = {format((Decimal(value) / 100).normalize(), 'f')
                     for value in attributes['wall_thickness_cm']}
        remaining_lengths = set(specs.get('dim_len', ())) - thickness
        if remaining_lengths:
            specs['dim_len'] = tuple(sorted(remaining_lengths))
        else:
            specs.pop('dim_len', None)
    if 'room' in attributes:
        contexts['room'] = tuple(sorted(attributes.pop('room')))
    locations = tuple(sorted((set(parsed.locations) | attributes.pop('surface', set()))
                             - context_locations))
    attributes.pop('boilerplate', None)
    if attributes.get('colour') and 'colour_count' not in attributes:
        attributes['colour_count'] = {str(len(attributes['colour']))}
    for key in ('coats', 'colour_count'):
        if key in attributes:
            specs[key] = tuple(sorted(attributes.pop(key)))
    scope = parsed.scope
    defaults: list[str] = []
    for profile in catalog().get('profiles', []):
        required_ops = set(profile['operations'])
        allowed_ops = required_ops | set(profile.get('allowed_extra_operations', []))
        if (parsed.object != profile['object'] or not required_ops <= set(parsed.operations) <= allowed_ops
                or sorted(materials) != sorted(profile['materials'])
                or any(not set(values) <= attributes.get(key, set())
                       for key, values in profile.get('attributes', {}).items())
                or any(set(values) & attributes.get(key, set())
                       for key, values in profile.get('excluded_attributes', {}).items())):
            continue
        values = profile['defaults']
        if scope is None:
            scope = values['scope']
            defaults.append(profile['id'] + ':scope')
        if not locations:
            locations = tuple(sorted(values['locations']))
            defaults.append(profile['id'] + ':locations')
        for key, value in values.get('specs', {}).items():
            if key not in specs:
                specs[key] = tuple(value)
                defaults.append(profile['id'] + ':spec:' + key)
        for key, value in values.get('attributes', {}).items():
            if key not in attributes:
                attributes[key] = set(value)
                defaults.append(profile['id'] + ':attribute:' + key)
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
    remainder = re.sub(r'\s*\((?:м[23²³]?|m[23²³]?|бр\.?)\)\s*$', ' ', remainder, flags=re.I)
    residual = {t for t in canonical_tokens(_head_text(remainder))
                if t not in _PROCESS_CONCEPTS and t not in _GENERIC_TERMS
                and not _SPEC_TOKEN_RE.fullmatch(t)}
    if parsed.object == 'paint':
        residual.discard('цвят')
    from app.modules.cost_match.bulgarian import replacement_bindings

    secondary = tuple(sorted((op, obj) for op, obj in replacement_bindings(text, parsed.object)
                             if obj != parsed.object))
    ids: dict[str, Any] = {'operations': [concept_id('operation', op) for op in sorted(parsed.operations)
                                         if op in catalog()['concept_ids']['operation']],
                           'materials': [concept_id('material', mat) for mat in sorted(materials)]}
    if parsed.object:
        ids['object'] = concept_id('object', parsed.object)
    if scope:
        ids['scope'] = concept_id('scope', scope)
    return CanonicalWork(
        definition_id=parsed.object if parsed.object in definitions() else None,
        object=parsed.object,
        operations=tuple(sorted(parsed.operations)), scope=scope,
        materials=tuple(sorted(materials)),
        specs=specs,
        inclusions=tuple(sorted(parsed.tail)), exclusions=tuple(sorted(parsed.excluded)),
        locations=locations,
        residual_terms=tuple(sorted(residual)),
        attributes={key: tuple(sorted(values)) for key, values in attributes.items()},
        contexts=contexts, secondary_work=secondary, defaults_applied=tuple(defaults), concept_ids=ids,
    )


@lru_cache(maxsize=1)
def catalog_digest() -> str:
    encoded = json.dumps(catalog(), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return sha256(encoded.encode('utf-8')).hexdigest()


def evidence_origin(metadata: dict[str, Any]) -> str | dict[str, Any]:
    explicit = metadata.get('evidence_origin')
    if isinstance(explicit, str | dict) and explicit:
        return explicit
    quotation = metadata.get('original_quotation')
    if not isinstance(quotation, dict):
        quotation = metadata
    origin = quotation.get('origin')
    if isinstance(origin, dict) and isinstance(origin.get('ref'), str) and origin['ref'].strip():
        return str(origin['ref']).strip()
    source_boq = metadata.get('source_boq')
    return str(source_boq) if source_boq else ''


def work_metadata(text: str) -> dict[str, Any]:
    work = canonical_work(text).as_dict()
    identity = {key: value for key, value in work.items() if key not in {'defaults_applied', 'contexts'}}
    encoded = json.dumps(identity, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return {'schema_version': catalog()['version'], 'catalog_digest': catalog_digest(), 'work': work,
            'action_vector': work['operations'],
            'target_vector': {key: work[key] for key in ('object', 'materials', 'locations', 'scope',
                              'attributes', 'specs', 'inclusions', 'exclusions', 'secondary_work')},
            'project_context': work['contexts'],
            'fingerprint': sha256(encoded.encode('utf-8')).hexdigest(),
            'interpretation_status': 'deterministic_unverified'}
