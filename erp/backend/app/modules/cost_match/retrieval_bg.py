"""Retrieve Bulgarian quotations by shared, versioned work signatures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from app.modules.cost_match.bulgarian import is_structural, ANCILLARY_OPS
from app.modules.cost_match.work_catalog import canonical_work, catalog_digest, work_metadata
from app.modules.cost_match.work_context import effective_work, is_work_fragment

SIGNATURE_INDEX_VERSION = 1
DESCRIPTION_WHITESPACE = (' \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680'
                         '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
                         '\u2028\u2029\u202f\u205f\u3000')


@dataclass(frozen=True)
class RetrievalGroup:
    kind: str
    value: str

    def matches(self, text: str) -> bool:
        work = canonical_work(text)
        if self.kind == 'object':
            return bool(work.object == self.value)
        return bool(self.value in getattr(work, self.kind + 's'))


# Site works bundled into bill lines (dig, refill, haul) are priced per
# object elsewhere in the corpus - a pipe quotation legitimately stays
# silent about the excavation around it. They must not veto retrieval.
# Defined in ``bulgarian`` (which cannot import this module) and re-exported.


@dataclass(frozen=True)
class RetrievalPlan:
    """Object identity is required; operations and materials veto only on
    genuine disjointness. A query for demolition never sees install-only
    rows, but a quote silent about its work type still surfaces for the
    matcher to judge. Modifier materials (fibres in concrete, the timber
    structure around insulation) cannot empty a pool - one shared material
    suffices, none declared is not a contradiction."""
    groups: tuple[RetrievalGroup, ...]
    query_ops: frozenset[str] = ()
    query_materials: frozenset[str] = ()
    blocked_reason: str | None = None
    exact_text: str | None = None

    def compatible(self, operations: Any, materials: Any) -> bool:
        ops = set(operations or ())
        mats = set(materials or ())
        # Veto, not containment: silence is not a contradiction. A quote that
        # declares a different operation family or a disjoint material is
        # excluded; a quote silent about them stays for the matcher to judge.
        if self.query_ops and ops and ops.isdisjoint(self.query_ops):
            return False
        if self.query_materials and mats and mats.isdisjoint(self.query_materials):
            return False
        return True

    def compatible_signature(self, work: dict[str, Any] | None) -> bool:
        work = work or {}
        return self.compatible(work.get('operations'), work.get('materials'))

    def matches(self, description: str, parents: list[str] | None = None) -> bool:
        evidence: str = effective_work(description, parents or [])
        if self.blocked_reason:
            return False
        if self.exact_text is not None:
            return evidence.strip().lower() == self.exact_text
        if not self.groups:
            return False
        work = canonical_work(evidence)
        for group in self.groups:
            if group.kind == 'object' and not group.matches(evidence):
                return False
        return self.compatible(work.operations, work.materials)

    def requirements(self) -> dict[str, Any]:
        required: dict[str, Any] = {}
        for group in self.groups:
            if group.kind == 'object':
                required['object'] = group.value
            else:
                required.setdefault(group.kind + 's', []).append(group.value)
        return required

    def as_dict(self) -> dict[str, Any]:
        return {'schema_version': 2, 'catalog_digest': catalog_digest(),
                'groups': [asdict(group) for group in self.groups],
                'query_ops': sorted(self.query_ops),
                'query_materials': sorted(self.query_materials),
                'blocked_reason': self.blocked_reason, 'exact_text': self.exact_text}


@lru_cache(maxsize=16384)
def retrieval_plan(text: str) -> RetrievalPlan:
    if is_structural(text) or is_work_fragment(text):
        return RetrievalPlan((), blocked_reason='work_context_missing' if is_work_fragment(text) else 'structural_row')
    work = canonical_work(text)
    if not work.object:
        return (RetrievalPlan((), exact_text=text.strip().lower()) if text.strip()
                else RetrievalPlan((), blocked_reason='work_object_unknown'))
    # ``groups`` reports the recognized concept groups (object, then
    # operations, then materials); only the object group is enforced as a
    # conjunct. Operations and materials act as disjointness vetoes via
    # ``compatible()``.
    required_ops = frozenset(work.operations) - ANCILLARY_OPS
    groups = [RetrievalGroup('object', work.object)]
    groups.extend(RetrievalGroup('operation', op) for op in sorted(required_ops))
    groups.extend(RetrievalGroup('material', m) for m in sorted(work.materials))
    return RetrievalPlan(
        tuple(groups),
        query_ops=required_ops,
        query_materials=frozenset(work.materials),
    )


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


def sql_predicate(plan: RetrievalPlan, model: Any) -> Any:
    from sqlalchemy import and_, cast, false, func
    from sqlalchemy.dialects.postgresql import JSONB

    if plan.blocked_reason:
        return false()
    if plan.exact_text is not None:
        upper = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЬЮЯ'
        quote = func.translate(func.lower(sql_description(model)), upper, upper.lower())
        return func.trim(quote) == plan.exact_text
    required = plan.requirements()
    if 'object' not in required:
        return false()
    work = cast(model.metadata_['canonical_work']['work'], JSONB)
    # Only the object conjunct is enforced in SQL; operation and material
    # disjointness vetoes are applied by the caller via
    # ``RetrievalPlan.compatible_signature`` on the stored signature.
    return and_(sql_current_signature(model),
                work.contains({'object': required['object']}))
