"""Retrieve Bulgarian quotations by shared, versioned work signatures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from app.modules.cost_match.bulgarian import is_structural, quotation_eligible
from app.modules.cost_match.work_catalog import (
    canonical_work,
    catalog_digest,
    concept_id,
    operator_pricing_rule,
    work_metadata,
)
from app.modules.cost_match.work_context import effective_work, is_work_fragment

SIGNATURE_INDEX_VERSION = 2
DESCRIPTION_WHITESPACE = (' \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680'
                         '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
                         '\u2028\u2029\u202f\u205f\u3000')


def literal_description(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n').strip().lower()


@dataclass(frozen=True)
class RetrievalGroup:
    kind: str
    value: str

    def matches(self, text: str) -> bool:
        work = canonical_work(text)
        if self.kind == 'object':
            return bool(work.object == self.value)
        return bool(self.value in getattr(work, self.kind + 's'))


@dataclass(frozen=True)
class RetrievalPlan:
    groups: tuple[RetrievalGroup, ...]
    blocked_reason: str | None = None
    exact_text: str | None = None
    allow_without_disassembly: bool = False
    candidate_code: str | None = None
    operator_rule_id: str | None = None

    def matches(self, description: str, parents: list[str] | None = None) -> bool:
        if not self.allow_without_disassembly and not quotation_eligible('', description):
            return False
        evidence: str = effective_work(description, parents or [])
        if self.blocked_reason:
            return False
        if self.exact_text is not None:
            return literal_description(evidence) == self.exact_text
        return bool(self.groups) and all(group.matches(evidence) for group in self.groups)

    def requirements(self) -> dict[str, Any]:
        required: dict[str, Any] = {}
        for group in self.groups:
            if group.kind == 'object':
                required['object'] = group.value
            else:
                required.setdefault(group.kind + 's', []).append(group.value)
        return required

    def id_requirements(self) -> dict[str, Any]:
        required: dict[str, Any] = {}
        for group in self.groups:
            value = concept_id(group.kind, group.value)
            if group.kind == 'object':
                required['object'] = value
            else:
                required.setdefault(group.kind + 's', []).append(value)
        return required

    def compatible_signature(self, work: dict[str, Any] | None) -> bool:
        if not isinstance(work, dict):
            return False
        requirements = self.requirements()
        return all(work.get(key) == value if key == 'object'
                   else set(value) <= set(work.get(key) or ())
                   for key, value in requirements.items())

    def as_dict(self) -> dict[str, Any]:
        return {'schema_version': 1, 'catalog_digest': catalog_digest(),
                'groups': [asdict(group) for group in self.groups],
                'blocked_reason': self.blocked_reason, 'exact_text': self.exact_text,
                'concept_ids': self.id_requirements(), 'allow_without_disassembly': self.allow_without_disassembly,
                'candidate_code': self.candidate_code, 'operator_rule_id': self.operator_rule_id}


@lru_cache(maxsize=16384)
def retrieval_plan(text: str, raw_query: str | None = None) -> RetrievalPlan:
    allow = quotation_eligible(text if raw_query is None else raw_query, 'без демонтаж')
    if is_structural(text) or is_work_fragment(text):
        return RetrievalPlan((), 'work_context_missing' if is_work_fragment(text) else 'structural_row')
    work = canonical_work(text)
    if not work.object:
        return (RetrievalPlan((), exact_text=literal_description(text), allow_without_disassembly=allow) if text.strip()
                else RetrievalPlan((), 'work_object_unknown'))
    groups = [RetrievalGroup('object', work.object)]
    groups.extend(RetrievalGroup('operation', operation) for operation in work.operations)
    groups.extend(RetrievalGroup('material', material) for material in work.materials)
    rule = operator_pricing_rule(text)
    return RetrievalPlan(tuple(groups), allow_without_disassembly=allow,
                         candidate_code=rule['code'] if rule else None,
                         operator_rule_id=rule['id'] if rule else None)


def quotation_text(item: Any) -> str:
    names = item.descriptions or {}
    bg = names.get('bg') if isinstance(names, dict) else None
    return str(bg).strip() if bg is not None and str(bg).strip() else str(item.description or '').strip()


def signature_metadata(item: Any) -> dict[str, Any]:
    quote = quotation_text(item)
    meta = item.metadata_ or {}
    context = meta.get('work_context') if isinstance(meta, dict) else None
    parents = [name for name in context if isinstance(name, str)] if isinstance(context, list) else []
    signature: dict[str, Any] = work_metadata(effective_work(quote, parents))
    signature['retrieval_index_version'] = SIGNATURE_INDEX_VERSION
    signature['index_source_description'] = quote
    signature['index_work_context'] = context
    return signature


def sql_description(model: Any) -> Any:
    from sqlalchemy import func

    return func.coalesce(func.nullif(func.btrim(model.descriptions['bg'].as_string(), DESCRIPTION_WHITESPACE), ''),
                         func.btrim(model.description, DESCRIPTION_WHITESPACE), '')


def sql_current_signature(model: Any) -> Any:
    from sqlalchemy import and_, cast, func, literal
    from sqlalchemy.dialects.postgresql import JSONB

    signature = model.metadata_['canonical_work']
    empty_context = cast(literal('null'), JSONB)
    return and_(
        signature['retrieval_index_version'].as_integer() == SIGNATURE_INDEX_VERSION,
        signature['catalog_digest'].as_string() == catalog_digest(),
        signature['index_source_description'].as_string() == sql_description(model),
        func.coalesce(cast(signature['index_work_context'], JSONB), empty_context)
        == func.coalesce(cast(model.metadata_['work_context'], JSONB), empty_context),
    )


def sql_literal_description(model: Any) -> Any:
    from sqlalchemy import func

    upper = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЬЮЯ'
    quote = func.translate(func.lower(sql_description(model)), upper, upper.lower())
    return func.replace(func.replace(quote, '\r\n', '\n'), '\r', '\n')


def sql_predicate(plan: RetrievalPlan, model: Any) -> Any:
    from sqlalchemy import and_, cast, false, true
    from sqlalchemy.dialects.postgresql import JSONB

    if plan.blocked_reason:
        return false()
    eligible: Any = (True if plan.allow_without_disassembly else
                ~sql_description(model).op('~*')(r'\mбез\s+демонтаж\M'))
    if plan.exact_text is not None:
        return and_(eligible, sql_literal_description(model) == plan.exact_text)
    if not plan.groups:
        return false()
    ids = cast(model.metadata_['canonical_work']['work']['concept_ids'], JSONB)
    return and_(eligible, sql_current_signature(model), ids.contains(plan.id_requirements()),
                model.code == plan.candidate_code if plan.candidate_code else true())
