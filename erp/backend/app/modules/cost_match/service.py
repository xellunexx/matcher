# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Cost-match business logic.

This is where the pure matcher meets the database. The split is deliberate and
worth keeping: :mod:`app.modules.cost_match.matcher` knows nothing about
sessions, rows or users and is therefore trivially testable, while everything
that has to persist, page, authorise or be reviewed lives here.

The three tiers
---------------
Every submitted line is scored against a bounded pool of cost items and lands
in exactly one tier:

* ``exact``           - the normalised descriptions are word-for-word equal and
                        the units agree. Nothing to argue about.
* ``high_confidence`` - scored at or above :data:`~matcher.HIGH_CONFIDENCE`.
* ``needs_review``    - scored between :data:`~matcher.REVIEW_CONFIDENCE` and
                        that. Plausible, and a person decides.
* ``unmatched``       - below the review floor. The closest candidate is still
                        recorded for context, exactly as the matcher offers it,
                        together with the matcher's own hint about what to try
                        next - but the tier does not claim it is a match.

Nothing is applied automatically
--------------------------------
The tier is what the machine found, never what the project adopts. A result
becomes usable only when a person confirms, overrides or rejects it, and that
ruling is written as its own row carrying the reviewer, the moment, and the
confidence the machine was showing at the time. An ``exact`` match is presented
first and still waits for a human. That is platform rule 7, and
``cost_match.decision_has_reviewer`` is the rule that catches a violation.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.modules.boq.models import Position

from app.core.events import event_bus
from app.modules.cost_match import events
from app.modules.cost_match.bases import is_named_base
from app.modules.cost_match.matcher import (
    HIGH_CONFIDENCE,
    REVIEW_CONFIDENCE,
    SCOPE_ANY,
    Candidate,
    MatchScore,
    best_match,
    canonical_tokens,
    explain,
    no_match_hint,
    normalize_text,
    normalize_unit,
    query_scope_intent,
    scopes_conflict,
    suggestion_rate,
    unit_rate_factor,
    work_rate_factor,
)
from app.modules.cost_match.models import (
    DECISION_CONFIRMED,
    DECISION_MANUAL,
    DECISION_OVERRIDDEN,
    DECISION_PENDING,
    DECISION_REJECTED,
    RUN_STATUS_CLOSED,
    RUN_STATUS_MATCHED,
    TIER_EXACT,
    TIER_HIGH_CONFIDENCE,
    TIER_NEEDS_REVIEW,
    TIER_UNMATCHED,
    WEB_ESTIMATE_SUGGESTED,
    MatchDecision,
    MatchPattern,
    MatchResult,
    MatchRun,
    WebEstimate,
)
from app.modules.cost_match.repository import (
    QUEUE_TIERS,
    CostBaseRepository,
    MatchDecisionRepository,
    MatchResultRepository,
    MatchRunRepository,
)
from app.modules.cost_match.schemas import (
    MAX_BATCH_LINES,
    BoqMatchRunCreate,
    BoqMatchRunResponse,
    CostMatchValidationReport,
    MatchDecisionCreate,
    MatchLineInput,
    MatchResultResponse,
    MatchRunCounts,
    MatchRunCreate,
    MatchRunResponse,
    MatchRunUpdate,
    WebEstimateResponse,
)
from app.modules.cost_match.semantic_feedback import decode_link, encode_link, semantic_links
from app.modules.cost_match.validators import (
    evaluate_result,
    evaluate_run,
    merge_reports,
)
from app.modules.cost_match.webverify import (
    WebVerifyError,
    estimate_signature,
    fetch_estimate,
)
from app.modules.costs.models import CostItem

logger = logging.getLogger(__name__)

# How many scored candidates are kept on each result so an override is a pick
# from a list rather than a fresh search. The winner is included, so the
# reviewer sees the runners-up next to what was chosen for them.
ALTERNATIVES_KEPT = 5

# Corpus rows whose prices are reference figures, not company-approved
# rates: unaudited web anchors and pending_review seed rows (Buildly
# material prices). They may suggest a rate - the TenderOps contract is
# "flagged REF" - but the result can never sit in an auto-trusted tier.
_REFERENCE_ORIGINS = frozenset({"reference_web", "supplier_web_anchor"})

# A line asking for work priced by a bare material rate (or a supply line
# priced by a labour/machine rate) is suspicious evidence, not none. The
# candidate stays reviewable under a soft penalty rather than being
# silently dropped from the pool.
_SCOPE_MISMATCH_PENALTY = Decimal("0.85")

# Confidence is stored with the same four-decimal precision the matcher
# produces, so a value never changes shape between scoring and reading.
_CONFIDENCE_PLACES = Decimal("0.0001")

# ``price_basis`` vocabulary written onto BOQ positions by ``run_boq_match``
# and kept in step by ``record_decision``. The tier is in the value itself so
# a price report can tell "priced from a confident corpus hit" apart from
# "reference figure below the review floor" without joining back to the run.
BOQ_BASIS_EXACT = "corpus_exact"
BOQ_BASIS_HIGH = "corpus_high"
BOQ_BASIS_REVIEW = "corpus_review"
BOQ_BASIS_REFERENCE = "corpus_reference"
BOQ_BASIS_CONFIRMED = "corpus_confirmed"
BOQ_BASIS_OVERRIDDEN = "corpus_override"
BOQ_BASIS_REJECTED = "corpus_rejected"
BOQ_BASIS_FX = "corpus_currency_mismatch"
BOQ_BASIS_UNIT = "corpus_unit_mismatch"
BOQ_BASIS_NO_MATCH = "corpus_no_match"
# A ``manual`` ruling: the reviewer typed the price (or adopted an AI
# estimate's figure) instead of adopting a corpus row. No ``corpus_``
# prefix - the number is human-authored evidence, same standing as the
# operator-declared bases below.
BOQ_BASIS_MANUAL = "manual"

# Bulgaria adopted the euro, so corpus rows still quoted in BGN are legacy
# prices carried over at the irrevocable conversion rate. Converting a BGN
# figure into an EUR position (or a legacy EUR figure the other way) is
# arithmetic on a fixed statutory constant, not a market guess - the applied
# rate and the original figure are recorded on the position for audit. Any
# other currency pair is still withheld, never converted.
BGN_PER_EUR = Decimal("1.95583")
_FIXED_CURRENCY_RATES = frozenset({"BGN", "EUR"})

_TIER_BASIS = {
    TIER_EXACT: BOQ_BASIS_EXACT,
    TIER_HIGH_CONFIDENCE: BOQ_BASIS_HIGH,
    TIER_NEEDS_REVIEW: BOQ_BASIS_REVIEW,
}

# Bases a match run wrote by itself, as opposed to ``corpus_confirmed`` /
# ``corpus_override`` which record a person's ruling. When a re-run withholds
# its new suggestion (currency or unit conflict), only an auto-written rate
# is cleared - a human-set price is never touched.
# Operator-declared ``price_basis`` values (the boq ``PRICE_BASIS_VALUES``
# vocabulary): a rate standing on one of these is human-authored evidence
# and may be learned into the corpus. ``corpus_*`` bases are machine output
# and confirming them proves nothing new.
_OPERATOR_PRICE_BASES = frozenset(
    {
        "invoice",
        "quotation",
        "price_list",
        "contract_rate",
        "norm",
        "historic",
        "judgement",
        BOQ_BASIS_MANUAL,
    }
)

_AUTO_WRITTEN_BASES = frozenset(
    {
        BOQ_BASIS_EXACT,
        BOQ_BASIS_HIGH,
        BOQ_BASIS_REVIEW,
        BOQ_BASIS_REFERENCE,
        BOQ_BASIS_FX,
        BOQ_BASIS_UNIT,
        BOQ_BASIS_REJECTED,
        BOQ_BASIS_NO_MATCH,
    }
)

# Bases a person set - typed prices, confirmations, overrides, and the
# operator-declared vocabulary. A re-run may record a new suggestion on such
# a line but must never move its rate, total or basis: human evidence is the
# strongest signal the system has, and losing it to a lexical re-match is
# exactly the regression the cascade exists to prevent.
_HUMAN_BASES = _OPERATOR_PRICE_BASES | {BOQ_BASIS_CONFIRMED, BOQ_BASIS_OVERRIDDEN}


class RunClosedError(RuntimeError):
    """Raised when a ruling is attempted on a run whose review is closed.

    Closing a run is how a surveyor says the pricing is settled. Letting a
    late ruling in silently would move a bill that has already been quoted
    from, so the run is re-opened deliberately or not at all.
    """

    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        super().__init__(f"match run {run_id} is closed and does not accept new decisions")


class NoSuggestionToConfirmError(ValueError):
    """Raised when confirming a result that never received a suggestion.

    There is nothing to confirm: the base returned no candidate for the line.
    The honest rulings here are an override onto an item the reviewer found
    themselves, or a rejection.
    """

    def __init__(self, result_id: uuid.UUID) -> None:
        self.result_id = result_id
        super().__init__(f"match result {result_id} has no suggestion to confirm")


class DecisionPayloadError(ValueError):
    """Raised when a ruling's payload contradicts the ruling itself.

    An override with no target is not an override, and a rejection that names
    a cost item is two different answers at once.
    """


class BoqLockedError(RuntimeError):
    """Raised when corpus pricing is attempted on a locked BOQ.

    A locked bill is a frozen document. Writing suggestions onto it would
    silently change the numbers a client may already have seen, so the run
    is refused rather than flagged.
    """

    def __init__(self, boq_id: uuid.UUID) -> None:
        self.boq_id = boq_id
        super().__init__(f"BOQ {boq_id} is locked and cannot be repriced")


# ── small helpers ───────────────────────────────────────────────────────────


def _decision_price(
    rate: Decimal | None,
    unit: str,
    currency: str,
    *,
    target_unit: str,
    project_currency: str,
    query_text: str = "",
    candidate_text: str = "",
) -> Decimal:
    if rate is None or not rate.is_finite() or rate <= 0:
        raise DecisionPayloadError("the adopted price must be finite and positive; enter a manual price")
    factor = work_rate_factor(unit, target_unit, query_text=query_text, candidate_text=candidate_text)
    if factor is None:
        raise DecisionPayloadError(
            f"cannot convert quotation unit '{unit}' to BOQ unit '{target_unit}'; "
            "choose a compatible item or enter a manual price per BOQ unit"
        )
    applied_rate: Decimal = rate * factor
    source_currency = (currency or "").strip().upper()
    target_currency = (project_currency or "").strip().upper()
    if not target_currency:
        raise DecisionPayloadError("set the project currency before adopting a price")
    if source_currency != target_currency:
        if {source_currency, target_currency} <= _FIXED_CURRENCY_RATES:
            applied_rate *= Decimal(1) / BGN_PER_EUR if target_currency == "EUR" else BGN_PER_EUR
        else:
            raise DecisionPayloadError(
                f"cannot convert quotation currency '{source_currency}' to project currency "
                f"'{target_currency}'; enter a manual price in project currency"
            )
    return applied_rate


def _quantise_confidence(value: float | Decimal) -> Decimal:
    """Bring a confidence onto the stored four-decimal Decimal scale.

    Goes through ``str`` rather than ``Decimal(float)`` so 0.75 stays 0.75
    instead of becoming 0.750000000000000055511151231257827.
    """
    try:
        return Decimal(str(value)).quantize(_CONFIDENCE_PLACES)
    except (InvalidOperation, ValueError, ArithmeticError):
        return Decimal("0").quantize(_CONFIDENCE_PLACES)


def _band(confidence: Decimal) -> str:
    """Traffic-light band for a stored confidence.

    Mirrors the matcher's own banding, read off the same two constants, so a
    stored result cannot be shown in a different colour from the one the
    scorer meant.
    """
    if confidence >= Decimal(str(HIGH_CONFIDENCE)):
        return "high"
    if confidence >= Decimal(str(REVIEW_CONFIDENCE)):
        return "medium"
    return "low"


def _localized_description(item: CostItem, locale: str) -> str:
    """The cost item's description in ``locale``, falling back sensibly.

    Cost bases carry per-locale descriptions in ``CostItem.descriptions``.
    Scoring against the reader's own language is not cosmetic: it puts the
    query and the candidate in the same vocabulary before the matcher folds
    them onto shared concepts, which is where a foreign bill gets its recall.
    Falls back to the base language of a regional tag (``de-AT`` to ``de``)
    and finally to the item's primary description.
    """
    descriptions = item.descriptions if isinstance(item.descriptions, dict) else {}
    for key in (locale, (locale or "").split("-")[0].split("_")[0]):
        if not key:
            continue
        value = descriptions.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return item.description or ""


def _dedupe_pool(items: list[CostItem], locale: str) -> list[CostItem]:
    """Collapse same-name non-Bulgarian candidates to their median-priced row.

    The corpus holds duplicate items - identical description, unit and
    scope at several rates - and input order used to pick which one
    priced the line (a price lottery). The median is deterministic and
    central; the losing duplicates still reach the reviewer through the
    ``dup_*`` spread factors on the result. Ties break on the lowest
    code so repeated runs pick the same representative. Bulgarian quotations
    are kept separate so canonical price disagreement cannot be hidden here.
    """
    from app.modules.cost_match.bulgarian import is_bulgarian

    if locale.split('-')[0].split('_')[0] == 'bg' or any(
        is_bulgarian(_localized_description(item, locale)) for item in items
    ):
        return list(items)
    groups: dict[tuple[str, str, str], list[CostItem]] = {}
    from app.modules.cost_match.work_context import effective_work

    for item in items:
        meta = item.metadata_ or {}
        context = meta.get('work_context')
        parents = [value for value in context if isinstance(value, str)] if isinstance(context, list) else []
        key = (
            normalize_text(effective_work(_localized_description(item, locale), parents)),
            (item.unit or "").strip().lower(),
            str(meta.get("scope") or ""),
        )
        groups.setdefault(key, []).append(item)
    out: list[CostItem] = []
    for members in groups.values():
        if len(members) == 1:
            out.append(members[0])
            continue
        rated = [(m, r) for m in members if (r := _parse_rate(m.rate)) is not None]
        if len(rated) < 2:
            out.append(members[0])
            continue
        vals = sorted(r for _, r in rated)
        mid = len(vals) // 2
        median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
        best = min(rated, key=lambda mr: (abs(mr[1] - median), str(mr[0].code)))
        out.append(best[0])
    return out


def _to_candidate(item: CostItem, locale: str) -> Candidate:
    """Wrap a cost item as a matcher candidate, money untouched.

    The rate travels in ``payload`` as the Decimal-string the base stores, so
    it reaches :func:`~matcher.suggestion_rate` without ever passing through a
    float.
    """
    meta = item.metadata_ or {}
    desc = _localized_description(item, locale)
    quotation = meta.get('original_quotation')
    if not isinstance(quotation, dict):
        quotation = {}
    money = meta.get('money')
    if not isinstance(money, dict):
        money = quotation.get('money')
    if not isinstance(money, dict):
        money = {}
    from app.modules.cost_match.work_catalog import evidence_origin, work_metadata
    from app.modules.cost_match.work_context import effective_work

    context = meta.get('work_context')
    parents = [value for value in context if isinstance(value, str)] if isinstance(context, list) else []
    evidence = effective_work(desc, parents)
    # Bulgaria is euro: a corpus row still quoted in BGN is a legacy lev
    # figure, carried at the irrevocable peg. Converting here - once, at
    # candidate build - means pooling, median, suggestion and writeback all
    # see one currency; mixing BGN and EUR rates in a median is meaningless.
    rate = item.rate
    currency = (item.currency or "").strip().upper()
    if currency == "BGN":
        parsed = _parse_rate(rate)
        if parsed is not None:
            rate = str(parsed / BGN_PER_EUR)
            currency = "EUR"
    return Candidate(
        ref=str(item.id),
        text=evidence,
        unit=item.unit,
        payload={
            "unit_rate": rate,
            "currency": currency,
            "quotation_rate": None if item.rate is None else str(item.rate),
            "quotation_currency": item.currency or "",
            "vat_included": (meta.get('vat_included') if meta.get('vat_included') is not None
                             else money.get('vatIncluded')),
            "auto_pricing_eligible": (meta.get('tenderops_status') != 'pending_review'
                                      and str(meta.get('origin_kind') or item.source or '') not in _REFERENCE_ORIGINS),
            "code": item.code,
            "source": item.source,
            "scope": str(meta.get("scope") or ""),
            "provenance_status": str(meta.get("tenderops_status") or ""),
            "origin_kind": str(meta.get("origin_kind") or item.source or ""),
            "display_text": desc,
            "canonical_work": work_metadata(evidence),
            "evidence_description": evidence,
            "work_context": parents,
            "bill_terms": meta.get("bill_terms"),
            "evidence_origin": evidence_origin(meta),
            "price_as_of": as_of.isoformat() if (as_of := getattr(item, "price_as_of", None)) else "",
            "created_at": (item.created_at.isoformat()
                           if getattr(item, "created_at", None) else ""),
        },
    )


def _tier_for(confidence: Decimal, factors: dict[str, Any], has_candidate: bool) -> str:
    """Place a scored line in one of the three tiers, or leave it unmatched.

    ``exact`` requires both the word-for-word equality the matcher reports in
    ``factors["exact"]`` **and** a confidence that survived the unit check:
    an exact text match priced per cubic metre against a line measured in
    square metres is not an exact match, it is a trap, and the matcher's unit
    penalty is what pushes it back down into review.
    """
    if not has_candidate:
        return TIER_UNMATCHED
    if confidence < Decimal(str(REVIEW_CONFIDENCE)):
        return TIER_UNMATCHED
    if confidence >= Decimal(str(HIGH_CONFIDENCE)):
        exact = factors.get("exact")
        if exact is not None and float(exact) >= 1.0:
            return TIER_EXACT
        return TIER_HIGH_CONFIDENCE
    return TIER_NEEDS_REVIEW


def _candidate_snapshot(
    candidate: Candidate, score: MatchScore, *, in_pool: bool = False
) -> dict[str, Any]:
    """One scored candidate as the JSON snapshot kept on the result.

    Money and confidence are stored as strings: a JSON column cannot hold a
    Decimal, and storing a float here would undo the precision the rest of the
    path is careful about. ``matched_tokens``/``unmatched_tokens`` carry the
    token-level evidence the reviewer validates - the bridge, not just the
    score it produced.
    """
    payload = candidate.payload or {}
    rate = suggestion_rate(candidate)
    return {
        "cost_item_id": candidate.ref,
        "code": str(payload.get("code") or ""),
        "description": str(payload.get("display_text") or candidate.text),
        "unit": candidate.unit or "",
        "rate": None if rate is None else format(rate, "f"),
        "currency": str(payload.get("currency") or ""),
        "quotation_rate": payload.get("quotation_rate"),
        "quotation_currency": str(payload.get("quotation_currency") or ""),
        "confidence": format(_quantise_confidence(score.confidence), "f"),
        "band": score.band,
        "reason_codes": list(score.reasons),
        "matched_tokens": list(score.shared_tokens),
        "unmatched_tokens": list(score.unanswered_tokens),
        "in_pool": in_pool,
        "canonical_work": payload.get("canonical_work"),
        "evidence_description": payload.get("evidence_description", candidate.text),
        "work_context": payload.get("work_context", []),
        "reference": bool(
            payload.get("provenance_status") == "pending_review"
            or payload.get("origin_kind") in _REFERENCE_ORIGINS
        ),
    }


class CostMatchService:
    """Persisting, scoring and reviewing cost-match runs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.run_repo = MatchRunRepository(session)
        self.result_repo = MatchResultRepository(session)
        self.decision_repo = MatchDecisionRepository(session)
        self.base_repo = CostBaseRepository(session)

    # ── creating and scoring a run ──────────────────────────────────────

    async def create_run(
        self,
        data: MatchRunCreate,
        *,
        created_by: uuid.UUID | None = None,
        on_line_done: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> MatchRun:
        """Score a submitted batch and persist the run with all its results.

        Identical lines are retrieved and scored once and the outcome is
        copied onto each of them. A pasted bill repeats itself constantly
        (the same "reinforced concrete C30/37" on eight floors), and scoring
        it eight times would multiply the database work for an answer that is
        deterministic anyway.

        Args:
            data: The batch, plus the cost base it is being priced against.
            created_by: The user submitting it, recorded on the run.

        Returns:
            The persisted run. Its results are queried through the repository,
            never through ``run.results`` - that relationship refuses lazy SQL
            by design.
        """
        run = MatchRun(
            project_id=data.project_id,
            name=data.name,
            source_label=data.source_label,
            source_locale=data.source_locale or "en",
            cost_source=data.cost_source,
            region=data.region,
            catalog_id=data.catalog_id,
            item_count=len(data.lines),
            candidate_limit=data.candidate_limit,
            created_by=created_by,
            tenant_id=data.tenant_id,
            notes=data.notes,
        )
        await self.run_repo.create(run)

        scored_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        rows: list[MatchResult] = []
        for index, line in enumerate(data.lines, start=1):
            key = (
                normalize_text(line.description),
                normalize_unit(line.unit) or normalize_text(line.unit),
                (line.source_ref or "").strip().lower(),
                normalize_text(line.raw_description if line.raw_description is not None else line.description),
            )
            if key not in scored_cache:
                scored_cache[key] = await self._score_line(
                    description=line.description,
                    unit=line.unit,
                    source_ref=line.source_ref,
                    run=run,
                    raw_query=line.raw_description,
                )
            if on_line_done is not None:
                on_line_done(index, scored_cache[key])
            rows.append(
                MatchResult(
                    run_id=run.id,
                    project_id=run.project_id,
                    line_no=index,
                    source_ref=line.source_ref,
                    source_description=line.raw_description if line.raw_description is not None else line.description,
                    source_unit=line.unit,
                    source_quantity=line.quantity,
                    **scored_cache[key],
                )
            )
        await self.result_repo.bulk_create(rows)

        counts = _counts_from_rows(rows)
        event_bus.publish_detached(
            events.MATCH_COMPLETED,
            {
                "run_id": str(run.id),
                "project_id": str(run.project_id),
                "item_count": run.item_count,
                "exact": counts.exact,
                "high_confidence": counts.high_confidence,
                "needs_review": counts.needs_review,
                "unmatched": counts.unmatched,
                "tenant_id": str(run.tenant_id) if run.tenant_id else None,
            },
            source_module=events.SOURCE_MODULE,
        )
        return run

    async def _score_line(
        self,
        *,
        description: str,
        unit: str,
        source_ref: str,
        run: MatchRun,
        raw_query: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve, score and shape one distinct line into result columns.

        Retrieval is bounded by the run's ``candidate_limit``; the code
        lookup, when the foreign bill carried one, adds at most one more row
        and is placed first so a tie with a code hit resolves toward it. The
        code never decides the tier on its own - it only widens what the
        matcher gets to look at.
        """
        from app.modules.cost_match.bulgarian import is_bulgarian, is_structural
        from app.modules.cost_match.work_context import is_work_fragment

        if is_structural(description) or is_work_fragment(description):
            reason = 'work_context_missing' if is_work_fragment(description) else 'structural_row'
            return {
                "tier": TIER_UNMATCHED,
                "confidence": Decimal("0").quantize(_CONFIDENCE_PLACES),
                "tie": False,
                "hint_code": reason,
                "reason_codes": [reason],
                "factors": {reason: 1.0},
                "alternatives": [],
                "suggested_cost_item_id": None,
                "suggested_code": "",
                "suggested_description": "",
                "suggested_unit": "",
                "suggested_rate": None,
                "suggested_currency": "",
            }
        items = await self.base_repo.find_candidates(
            description,
            cost_source=run.cost_source,
            region=run.region,
            catalog_id=run.catalog_id,
            unit=unit,
            limit=run.candidate_limit,
            raw_query=raw_query,
        )
        # Semantic recall: the lexical predicate alone misses paraphrased or
        # differently-worded bill lines ("Разрушаване ... с багер-чук"). The
        # embedding index only widens the candidate pool - tiering stays
        # deterministic in best_match below, which is the same contract the
        # code lookup above follows.
        items = await self._vector_candidates(description, items, run=run)
        remembered = await self._remembered_candidates(description, run)
        seen_items = {item.id for item in items}
        items.extend(item for item in remembered if item.id not in seen_items)
        if source_ref:
            coded = await self.base_repo.find_by_code(
                source_ref,
                cost_source=run.cost_source,
                region=run.region,
                catalog_id=run.catalog_id,
            )
            if coded is not None and all(existing.id != coded.id for existing in items):
                from app.modules.cost_match.retrieval_bg import retrieval_plan

                evidence = _to_candidate(coded, 'bg' if is_bulgarian(description) else run.source_locale)
                if not is_bulgarian(description) or retrieval_plan(description, raw_query).matches(evidence.text):
                    items = [coded, *items]

        items = _dedupe_pool(items, run.source_locale)
        candidates = [_to_candidate(item, 'bg' if is_bulgarian(description) else run.source_locale) for item in items]
        # Learned rulings adjust the contest: a bridge a reviewer confirmed
        # before lifts the candidate that offers it; one they refused sinks
        # it. The prior travels through the candidate payload so the matcher
        # itself stays free of I/O.
        priors = await self._pattern_priors(description, candidates, run)
        for cand in candidates:
            prior = priors.get(cand.ref, 1.0)
            if prior != 1.0 and isinstance(cand.payload, dict):
                cand.payload["pattern_prior"] = prior
        outcome = best_match(
            description,
            candidates,
            query_unit=unit,
            locale=run.source_locale,
            top_n=ALTERNATIVES_KEPT,
            raw_query=raw_query,
        )

        scored_all = outcome.scored_all or outcome.alternatives
        # The review queue shows a decision, not the phone book: at most
        # ALTERNATIVES_KEPT rows, and only ones a person could plausibly
        # confirm. Hard-zeroed rows carry a declared contradiction (domain,
        # object, spec) - they are evidence of what the line is NOT, and
        # the picker is no place for them.
        plausible = [
            (cand, score) for cand, score in scored_all
            if score.confidence > 0 or score.factors.get("pattern_prior")
        ][:ALTERNATIVES_KEPT]
        alternatives = []
        for cand, score in plausible:
            snapshot = _candidate_snapshot(cand, score, in_pool=cand.ref in outcome.pool_refs)
            snapshot['semantic_links'] = semantic_links(description, cand.text)
            alternatives.append(snapshot)
        if outcome.candidate is None or outcome.score is None:
            return {
                "tier": TIER_UNMATCHED,
                "confidence": Decimal("0").quantize(_CONFIDENCE_PLACES),
                "tie": outcome.tie,
                "hint_code": _hint_code(outcome.hint, description, candidates),
                "reason_codes": [],
                "factors": {},
                "alternatives": alternatives,
                "suggested_cost_item_id": None,
                "suggested_code": "",
                "suggested_description": "",
                "suggested_unit": "",
                "suggested_rate": None,
                "suggested_currency": "",
            }

        confidence = _quantise_confidence(outcome.score.confidence)
        payload = outcome.candidate.payload or {}
        factors: dict[str, Any] = {name: float(value) for name, value in outcome.score.factors.items()}
        from app.modules.cost_match.work_catalog import work_metadata

        factors['query_work'] = work_metadata(description)
        factors['raw_kcc_description'] = description if raw_query is None else raw_query
        factors['effective_work_description'] = description
        if 'no_equivalent_price_evidence' in outcome.score.reasons:
            factors['price_evidence_status'] = 'no_evidence'
        if outcome.pool_size:
            factors['equivalent_price_pool'] = {
                'method': 'median' if outcome.median_rate is not None else 'single_or_divergent',
                'unit': unit, 'currency': 'EUR' if outcome.median_rate is not None else payload.get('currency'),
                'rate': None if outcome.median_rate is None else str(outcome.median_rate),
                'min': str(outcome.pool_min), 'max': str(outcome.pool_max),
                'observations': [_candidate_snapshot(c, s, in_pool=True)
                                 for c, s in outcome.scored_all if c.ref in outcome.pool_refs],
            }
        if any(c.ref in outcome.pool_refs and not (c.payload or {}).get('auto_pricing_eligible', True)
               for c, _ in outcome.scored_all):
            factors['reference_price'] = True
        reasons = list(outcome.score.reasons)

        # Scope intent: the line's verb prefix declares which price kind it
        # needs. A candidate whose stored scope contradicts it keeps its
        # evidence but pays a soft penalty, marked on the result.
        intent = query_scope_intent(description)
        scope = str(payload.get("scope") or "")
        scope_conflicted = scopes_conflict(intent, scope)
        if scope_conflicted:
            factors["scope_alignment"] = 0.0
            confidence = _quantise_confidence(confidence * _SCOPE_MISMATCH_PENALTY)
            reasons.append("scope_mismatch")
        elif intent != SCOPE_ANY and scope:
            factors["scope_alignment"] = 1.0

        # Reference-priced rows price as flagged REF: the rate stays
        # visible but the result can never sit in an auto-trusted tier.
        is_reference = (
            bool(factors.get('reference_price'))
            or
            payload.get("provenance_status") == "pending_review"
            or payload.get("origin_kind") in _REFERENCE_ORIGINS
        )
        if is_reference:
            reasons.append("reference_price")
        tier = _tier_for(confidence, factors, has_candidate=True)
        if not outcome.is_confident and tier in (TIER_EXACT, TIER_HIGH_CONFIDENCE):
            tier = TIER_NEEDS_REVIEW
        if is_reference and tier != TIER_UNMATCHED:
            tier = TIER_NEEDS_REVIEW
        # A cross-scope price is evidence, never auto-trust: the material
        # rate that priced a work line stays needs_review so a person sees
        # the substitution instead of inheriting it silently.
        if scope_conflicted and tier != TIER_UNMATCHED:
            tier = TIER_NEEDS_REVIEW

        # Equivalent Bulgarian quotations use a median; the selected source
        # remains evidence, not a claim that it quoted the calculated rate.
        suggested_item = outcome.candidate
        suggested_unit = outcome.candidate.unit or ""
        suggested_rate = suggestion_rate(outcome.candidate)
        suggested_payload = payload
        if outcome.median_rate is not None and outcome.median_candidate is not None:
            suggested_item = outcome.median_candidate
            suggested_payload = suggested_item.payload or {}
            suggested_unit = unit
            suggested_rate = outcome.median_rate
            factors["pool_size"] = float(outcome.pool_size)
            factors["pool_min"] = float(outcome.pool_min or 0)
            factors["pool_max"] = float(outcome.pool_max or 0)
            factors["pool_median"] = float(outcome.median_rate)
            reasons.append("pooled_median")
            if outcome.pool_diverged:
                factors["pool_divergence"] = 1.0
                reasons.append("pool_divergence")
                if tier != TIER_UNMATCHED:
                    tier = TIER_NEEDS_REVIEW
        elif outcome.pool_diverged:
            # Diverged pools produce no median (a blended figure would be no
            # supplier's real price), but the divergence itself is review
            # evidence: the corpus carries two price regimes for this line.
            factors["pool_divergence"] = 1.0
            reasons.append("pool_divergence")
            if tier != TIER_UNMATCHED:
                tier = TIER_NEEDS_REVIEW

        factors['semantic_links'] = semantic_links(description, suggested_item.text)
        if suggested_payload.get('human_semantic_links'):
            factors['human_semantic_links'] = suggested_payload['human_semantic_links']

        # Duplicate-price evidence: rows sharing the winner's name and unit
        # carry a rate spread the reviewer should see rather than
        # silently inheriting whichever row won the ordering.
        spread = await self._price_spread(
            str(suggested_payload.get("display_text") or suggested_item.text),
            unit=suggested_item.unit,
            winner_source=str(suggested_payload.get("source") or ""),
        )
        if spread is not None:
            factors.update(spread)
            if spread["dup_count"] > 1:
                reasons.append("price_spread")
            # The corpus disagreeing with itself is not a confidence the
            # machine may keep: identical name+unit rows at rates more than
            # 1.5x apart mean the base holds two real prices and only a
            # person can say which one belongs to this bill. The suggestion
            # stays visible; the tier drops out of every auto-trusted band.
            if (
                spread["dup_count"] > 1
                and spread["dup_rate_max"] > spread["dup_rate_min"] * 1.5
                and tier in (TIER_EXACT, TIER_HIGH_CONFIDENCE)
            ):
                reasons.append("corpus_price_disagreement")
                tier = TIER_NEEDS_REVIEW

        # Semantic guard (optional, detachable): when the embedding backend
        # is enabled and the suggestion sits below the calibrated similarity
        # floor, the two texts do not mean the same thing whatever the shared
        # tokens say - demote to the review tier with the measured similarity
        # on the record. Two silences are deliberate: exact (word-for-word)
        # pairs carry the strongest possible evidence, and pairs where every
        # content concept of the line appears in the candidate head ("Pinus
        # mugo pumilio" inside "Pinus mugo pumilio. Планински бор джудже...")
        # are diluted in embedding space precisely because the candidate is
        # MORE complete - a vector must not veto lexical identity.
        if tier in (TIER_EXACT, TIER_HIGH_CONFIDENCE) and not factors.get("exact") and not is_bulgarian(description):
            from app.modules.cost_match import semantic
            from app.modules.cost_match.matcher import (
                _GENERIC_TERMS,
                _PROCESS_CONCEPTS,
                canonical_tokens,
            )

            cand_display = str(suggested_payload.get("display_text") or suggested_item.text)
            q_content = set(canonical_tokens(description)) - _PROCESS_CONCEPTS - _GENERIC_TERMS
            c_content = set(canonical_tokens(cand_display)) - _PROCESS_CONCEPTS - _GENERIC_TERMS
            if not (q_content and q_content <= c_content):
                sim = await semantic.veto(description, cand_display)
                if sim is not None:
                    factors["semantic_similarity"] = round(sim, 4)
                    reasons.append("low_semantic_similarity")
                    tier = TIER_NEEDS_REVIEW

        return {
            "tier": tier,
            "confidence": confidence,
            "tie": outcome.tie,
            "hint_code": _hint_code(outcome.hint, description, candidates),
            "reason_codes": reasons,
            "factors": factors,
            "alternatives": alternatives,
            "suggested_cost_item_id": uuid.UUID(suggested_item.ref),
            "suggested_code": str(suggested_payload.get("code") or ""),
            "suggested_description": str(
                suggested_payload.get("display_text") or suggested_item.text
            ),
            "suggested_unit": suggested_unit,
            "suggested_rate": suggested_rate,
            "suggested_currency": str(suggested_payload.get("currency") or ""),
        }

    async def _pattern_priors(
        self, description: str, candidates: list[Candidate], run: MatchRun
    ) -> dict[str, float]:
        """Apply current explicit rulings on this exact text and cost-base scope."""
        patterns = await self._semantic_patterns(description, run)
        priors: dict[str, float] = {}
        for cand in candidates:
            if not isinstance(cand.payload, dict):
                continue
            payload = cand.payload
            payload.pop('human_semantic_rejection', None)
            payload.pop('human_semantic_links', None)
            links = {link['id']: link for link in semantic_links(description, cand.text)}
            accepted: dict[str, dict[str, str]] = {}
            for pattern in patterns:
                if pattern.candidate_text != payload.get('display_text', cand.text):
                    continue
                for value in pattern.shared_tokens or []:
                    lesson = decode_link(value)
                    if (lesson is None or lesson['id'] not in links
                            or lesson['unit'] != (cand.unit or '')
                            or lesson['code'] != payload.get('code')):
                        continue
                    accepted[lesson['role']] = lesson
            if not accepted:
                continue
            if any(link['verdict'] == 'different' for link in accepted.values()):
                payload['human_semantic_rejection'] = True
                continue
            payload['human_semantic_links'] = [links[link['id']] for link in accepted.values()]
            priors[cand.ref] = round(min(1.0 + 0.05 * len(accepted), 1.25), 4)
        return priors

    async def _remembered_candidates(self, description: str, run: MatchRun) -> list[CostItem]:
        from app.modules.cost_match.bulgarian import is_bulgarian

        if not is_bulgarian(description):
            return []
        patterns = await self._semantic_patterns(description, run)
        ids = list(dict.fromkeys(pattern.cost_item_id for pattern in patterns
                                 if pattern.cost_item_id is not None and any(
                                     (lesson := decode_link(value)) is not None
                                     and lesson['verdict'] == 'same'
                                     for value in pattern.shared_tokens or [])))
        if not ids:
            return []
        items = await self.base_repo.find_by_ids(
            ids, cost_source=run.cost_source, region=run.region, catalog_id=run.catalog_id,
        )
        codes = {lesson['code'] for pattern in patterns for value in pattern.shared_tokens or []
                 if (lesson := decode_link(value)) is not None and lesson['verdict'] == 'same'}
        for code in sorted(codes - {item.code for item in items}):
            active = await self.base_repo.find_by_code(
                code, cost_source=run.cost_source, region=run.region, catalog_id=run.catalog_id,
            )
            if active is not None:
                items.append(active)
        candidates = [_to_candidate(item, 'bg') for item in items]
        priors = await self._pattern_priors(description, candidates, run)
        return [item for item, candidate in zip(items, candidates, strict=True)
                if candidate.ref in priors]

    async def _semantic_patterns(self, description: str, run: MatchRun) -> list[MatchPattern]:
        stmt = (
            select(MatchPattern)
            .join(MatchDecision, MatchDecision.id == MatchPattern.decision_id)
            .join(MatchRun, MatchRun.id == MatchPattern.run_id)
            .where(MatchPattern.query_text == description,
                   MatchRun.cost_source == run.cost_source,
                   MatchRun.region == run.region,
                   MatchRun.catalog_id == run.catalog_id,
                   MatchRun.tenant_id == run.tenant_id)
            .order_by(MatchDecision.created_at, MatchDecision.seq, MatchDecision.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _record_pattern(
        self, run: MatchRun, result: MatchResult, decision: MatchDecision,
        chosen_links: list[tuple[dict[str, str], str, CostItem]] | None = None,
    ) -> None:
        """Persist only expressly judged roles, with the exact two quoted texts."""
        if not chosen_links:
            return
        query = (result.factors or {}).get('effective_work_description') or result.source_description
        grouped: dict[uuid.UUID, tuple[CostItem, list[str]]] = {}
        for link, verdict, item in chosen_links:
            if item.id not in grouped:
                grouped[item.id] = (item, [])
            grouped[item.id][1].append(encode_link(link, verdict, item.code, item.unit))
        rows: list[MatchPattern] = []
        for item, encoded_links in grouped.values():
            cand_text = _localized_description(item, 'bg')
            rows.append(MatchPattern(
                decision_id=decision.id,
                result_id=result.id,
                run_id=result.run_id,
                project_id=result.project_id,
                verdict=decision.decision,
                query_text=query,
                query_tokens=list(canonical_tokens(query)),
                candidate_text=cand_text,
                candidate_tokens=list(canonical_tokens(cand_text)),
                shared_tokens=encoded_links,
                cost_item_id=item.id,
            ))
        self.session.add_all(rows)

    async def _price_spread(
        self, description: str, *, unit: str | None, winner_source: str = ""
    ) -> dict[str, float] | None:
        """Rate spread across corpus rows sharing the winner's name+unit.

        Duplicate-named items at different rates are a price lottery: today
        input order picks the winner. The factors carry count/min/max so the
        reviewer sees the spread instead of inheriting one arbitrary rate.
        Only numeric rates count; non-numeric strings are skipped.

        The contest obeys the corpus doctrine: a suggestion is judged by
        rows of its own class - reference evidence never vetoes an operator
        ruling and a pricelist twin never vetoes a confirmed line ruling -
        and when every peer carries a declared day the newest cohort stands
        alone, since a superseded ruling cannot veto its successor.
        """
        if not description.strip():
            return None
        from app.modules.cost_match.retrieval_bg import literal_description, sql_literal_description

        stmt = (
            select(CostItem.rate, CostItem.currency, CostItem.unit,
                   CostItem.source, CostItem.price_as_of, CostItem.created_at)
            .where(CostItem.is_active.is_(True))
            .where(
                sql_literal_description(CostItem) == literal_description(description)
            )
            .where(CostItem.rate.op("~")("^[0-9]+(\\.[0-9]+)?$"))
        )
        rows = (await self.session.execute(stmt)).all()
        # Rates are restated in EUR before comparing - a BGN figure is the
        # same money at the statutory peg, a foreign denomination is left
        # out of the spread rather than mixed in as if it were euros.
        peers: list[tuple[Decimal, str, str]] = []
        for rate_raw, currency, row_unit, source, as_of, created in rows:
            factor = unit_rate_factor(row_unit, unit) if unit else Decimal(1)
            if factor is None:
                continue
            parsed = _parse_rate(rate_raw)
            if parsed is None:
                continue
            cur = (currency or "").strip().upper()
            if cur == "BGN":
                parsed = parsed / BGN_PER_EUR
            elif cur != "EUR":
                continue
            day = str(as_of or "")[:10]
            if not day and source == "estimate_confirmed":
                day = str(created or "")[:10]
            peers.append((parsed * factor, str(source or ""), day))
        if not peers:
            return None
        from app.modules.cost_match.matcher import _OPERATOR_PRICE_SOURCES
        winner_operator = winner_source in _OPERATOR_PRICE_SOURCES
        peers = [p for p in peers
                 if (p[1] in _OPERATOR_PRICE_SOURCES) == winner_operator]
        confirmed = [p for p in peers if p[1] == "estimate_confirmed"]
        if confirmed:
            peers = confirmed
        if peers and all(day for _, _, day in peers):
            newest = max(day for _, _, day in peers)
            peers = [p for p in peers if p[2] == newest]
        rates = [rate for rate, _, _ in peers]
        if not rates:
            return None
        return {
            "dup_count": float(len(rates)),
            "dup_rate_min": float(min(rates)),
            "dup_rate_max": float(max(rates)),
        }

    async def _vector_candidates(
        self,
        description: str,
        items: list[CostItem],
        *,
        run: MatchRun,
    ) -> list[CostItem]:
        """Append embedding-index hits the lexical recall missed.

        Returns ``items`` unchanged whenever the vector path is unavailable
        (no embedder, no index) - lexical-only scoring is the documented
        fallback and a run must never fail on a missing optional index.
        The pool stays capped at ``run.candidate_limit``; semantic hits are
        appended after the lexical ones so a full lexical pool is unchanged.
        """
        from app.modules.cost_match.bulgarian import is_bulgarian

        if is_bulgarian(description) or not description.strip() or len(items) >= run.candidate_limit:
            return items
        try:
            from app.modules.costs import vector_adapter
        except Exception:  # optional extra not installed
            return items
        try:
            hits = await vector_adapter.search(
                description,
                limit=run.candidate_limit,
                region=run.region,
                # The vector index stores raw source tags, not named bases -
                # a base key would filter every hit out, so it passes None and
                # ``find_by_ids`` re-applies the base scope on the way back.
                source=(
                    None
                    if run.cost_source in (None, "all", "*") or is_named_base(run.cost_source)
                    else run.cost_source
                ),
            )
        except Exception:
            return items
        ids = []
        for h in hits:
            try:
                ids.append(uuid.UUID(str(h.get("id"))))
            except (ValueError, TypeError):
                continue
        if not ids:
            return items
        extra = await self.base_repo.find_by_ids(
            ids,
            cost_source=run.cost_source,
            region=run.region,
            catalog_id=run.catalog_id,
        )
        seen = {item.id for item in items}
        merged = list(items)
        for item in extra:
            if item.id not in seen:
                merged.append(item)
                seen.add(item.id)
                if len(merged) >= run.candidate_limit:
                    break
        return merged

    # ── BOQ-driven matching ─────────────────────────────────────────────

    async def run_boq_match(
        self,
        *,
        boq_id: uuid.UUID,
        data: BoqMatchRunCreate,
        created_by: uuid.UUID | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> BoqMatchRunResponse:
        """Match a whole BOQ against the cost base and price it in one pass.

        The submitted lines are the bill's own positions - description, unit,
        quantity and the position's ``reference_code`` as ``source_ref``, which
        is exactly the field's documented use. Runs are created through
        :meth:`create_run` in ``MAX_BATCH_LINES`` chunks, so the whole audit
        trail (results, tiers, review queue, decisions) is the ordinary one.

        Write-back is deliberately the reverse of the review-queue contract:
        a suggestion the matcher offered goes onto the position immediately
        with the tier recorded as ``price_basis`` (``corpus_*``), and a line
        with no offer stays untouched - unresolved, not guessed. Every ruling
        a person then records through :meth:`record_decision` re-syncs the
        position (confirmed price, override, or cleared on rejection), so the
        estimate and the queue cannot disagree.
        """
        from app.modules.boq.models import BOQ
        from app.modules.cost_match.bulgarian import is_structural
        from app.modules.projects.models import Project

        boq = await self.session.get(BOQ, boq_id)
        if boq is None:
            raise LookupError(f"BOQ {boq_id} not found")
        if boq.is_locked:
            raise BoqLockedError(boq.id)

        # The corpus is mixed-currency; a suggestion in a different currency
        # than the project's must be flagged, never applied as if converted.
        project = await self.session.get(Project, boq.project_id)
        project_currency = (project.currency or "").strip().upper() if project else ""

        # Sections and header rows are not priceable lines - same filter the
        # import-side callers apply.
        for position in boq.positions:
            if is_structural(position.description) and (position.price_basis or "") in _AUTO_WRITTEN_BASES:
                position.unit_rate = "0"
                position.total = "0"
                position.price_basis = BOQ_BASIS_NO_MATCH
        positions = [
            p
            for p in boq.positions
            if (p.unit or "").strip().lower() not in ("", "section")
            and (p.description or "").strip()
            and not is_structural(p.description)
        ]
        from app.modules.cost_match.work_context import effective_work, position_contexts

        contexts = position_contexts(boq.positions)
        for position in positions:
            meta = dict(position.metadata_ or {})
            meta['cost_match_work_context'] = contexts.get(position.id, [])
            position.metadata_ = meta
        if not positions:
            return BoqMatchRunResponse(
                boq_id=boq.id,
                run_ids=[],
                lines=0,
                counts=MatchRunCounts(),
                positions_priced=0,
                positions_unpriced=0,
            )

        run_ids: list[uuid.UUID] = []
        tier_counts = dict.fromkeys((TIER_EXACT, TIER_HIGH_CONFIDENCE, TIER_NEEDS_REVIEW, TIER_UNMATCHED), 0)
        priced = 0
        unpriced = 0

        # Base cascade: ``bases`` lists the databases to try in order, and
        # each stage only receives the positions the earlier stages left
        # without a rate - a line priced by SEK never reaches the operator
        # stage, so the most authoritative base always wins first. A stage is
        # an ordinary run pinned to that base, so the review queue and the
        # audit trail keep the same shape as a single-base match.
        base_list = (
            [b.strip() for b in data.bases if (b or "").strip()]
            if data.bases
            else [data.cost_source or "all"]
        )
        remaining = list(positions)
        settled: set[uuid.UUID] = set()

        def _settle_progress(position: Position, scored: dict[str, Any], last_stage: bool) -> None:
            # The bar counts lines, not line-evaluations: a position settles
            # the moment any base prices it, and unpriced lines settle only
            # after the final base - so 542 positions read 0..542, not 2168.
            if position.id in settled or on_progress is None:
                return
            offered = (scored.get('suggested_rate') is not None
                       and scored.get('tier') != TIER_UNMATCHED)
            if offered or last_stage:
                settled.add(position.id)
                on_progress(len(settled), len(positions))

        for base_index, base in enumerate(base_list):
            if not remaining:
                break
            last_stage = base_index == len(base_list) - 1
            stage_priced: set[uuid.UUID] = set()
            stage_positions = remaining
            for start in range(0, len(stage_positions), MAX_BATCH_LINES):
                chunk = stage_positions[start : start + MAX_BATCH_LINES]
                lines = [
                    MatchLineInput(
                        description=effective_work(p.description or '', contexts.get(p.id, []))[:4000],
                        raw_description=(p.description or '')[:4000],
                        unit=(p.unit or "")[:40],
                        quantity=_parse_rate(p.quantity),
                        source_ref=(p.reference_code or "")[:100],
                    )
                    for p in chunk
                ]
                def on_scored(
                    n: int, scored: dict[str, Any],
                    _chunk: list[Position] = chunk, _last: bool = last_stage,
                ) -> None:
                    _settle_progress(_chunk[n - 1], scored, _last)

                run = await self.create_run(
                    MatchRunCreate(
                        project_id=boq.project_id,
                        name=f"{boq.name} - {base} match {start + 1}-{start + len(chunk)}",
                        source_label=boq.name or f"BOQ {boq.id}",
                        source_locale=data.source_locale or "bg",
                        cost_source=base,
                        region=data.region,
                        catalog_id=data.catalog_id,
                        candidate_limit=data.candidate_limit,
                        notes=json.dumps({"boq_id": str(boq.id), "base": base,
                                          "positions": [str(p.id) for p in chunk],
                                          "work_context": {str(p.id): {'original': p.description,
                                                                      'parents': contexts.get(p.id, [])}
                                                           for p in chunk if contexts.get(p.id)}}),
                        lines=lines,
                    ),
                    created_by=created_by,
                    on_line_done=on_scored if on_progress is not None else None,
                )
                run_ids.append(run.id)

                results = await self.result_repo.list_all_for_run(run.id)
                for result in results:
                    tier_counts[result.tier] = tier_counts.get(result.tier, 0) + 1
                    index = result.line_no - 1
                    if not (0 <= index < len(chunk)):
                        continue
                    position = chunk[index]
                    if result.suggested_rate is None or result.tier == TIER_UNMATCHED:
                        # A re-run that offers nothing - or finds only a candidate
                        # it cannot stand behind (unmatched) - retracts the earlier
                        # corpus price with it, but only one this path wrote; a
                        # number a person typed or confirmed survives the re-match.
                        # The unmatched candidate stays on the result row for
                        # context in the review queue; it just never becomes money.
                        if (position.price_basis or "") in _AUTO_WRITTEN_BASES:
                            position.unit_rate = "0"
                            position.total = "0"
                            position.price_basis = BOQ_BASIS_NO_MATCH
                        # A line that survives the final base is settled too:
                        # the denominator is the position count, not positions
                        # x bases - the bar reads 542 lines, never 2168.
                        if last_stage and position.id not in settled:
                            settled.add(position.id)
                            if on_progress is not None:
                                on_progress(len(settled), len(positions))
                        continue
                    if self._price_position_from_result(
                        position, run, result, project_currency=project_currency
                    ):
                        priced += 1
                        stage_priced.add(position.id)
                        if position.id not in settled:
                            settled.add(position.id)
                            if on_progress is not None:
                                on_progress(len(settled), len(positions))
            remaining = [p for p in stage_positions if p.id not in stage_priced]

        # Positions still standing after the last base are the unpriced set:
        # counting them here keeps a line retried through three stages from
        # being tallied as unpriced three times.
        unpriced = len(remaining)

        await self.session.flush()
        return BoqMatchRunResponse(
            boq_id=boq.id,
            run_ids=run_ids,
            lines=len(positions),
            counts=MatchRunCounts(
                total=len(positions),
                exact=tier_counts[TIER_EXACT],
                high_confidence=tier_counts[TIER_HIGH_CONFIDENCE],
                needs_review=tier_counts[TIER_NEEDS_REVIEW],
                unmatched=tier_counts[TIER_UNMATCHED],
                pending=len(positions),
                queue_length=tier_counts[TIER_NEEDS_REVIEW] + tier_counts[TIER_UNMATCHED],
            ),
            positions_priced=priced,
            positions_unpriced=unpriced,
        )

    def _price_position_from_result(
        self,
        position: Position,
        run: MatchRun,
        result: MatchResult,
        *,
        project_currency: str = "",
    ) -> bool:
        """Write one suggestion onto its BOQ position, evidence attached.

        ``unit_rate`` gets the corpus figure, ``price_basis`` the tier it was
        found at, and the full provenance (run, result, code, currency, item)
        goes to ``metadata_["cost_match"]`` so the price report can trace
        every populated line back to its evidence row.

        A suggestion quoted in a different currency than the project's is NOT
        applied - a 185 BGN figure is not a 185 EUR figure and there is no
        conversion here to make it one. The exception is the BGN/EUR pair,
        joined at the irrevocable peg ``BGN_PER_EUR``: conversion is a fixed
        statutory constant, so the corpus figure lands converted and the
        original rate is kept in the evidence block. Any other pair is
        withheld and flagged ``currency_mismatch``.

        A suggestion whose unit cannot honestly convert to the position's is
        likewise withheld - an exact-description row priced per piece does
        not price a line measured in cubic metres, and a catalogue row priced
        per "комплект" does not price a per-kg line. Conversion goes through
        :func:`unit_rate_factor`, so bulk units ("100 м3", "10 броя") and
        magnitude gaps (per tonne vs per kg) land at the right scale. The
        line counts as unresolved until a person rules; a stale rate written
        by an earlier corpus run is cleared when the new suggestion is
        withheld, so an unsupported price never lingers under a mismatch
        flag. Returns ``True`` when the rate was applied, ``False`` when it
        was withheld.
        """
        rate = result.suggested_rate
        if rate is None or not rate.is_finite() or rate <= 0:
            return False
        suggestion_currency = (result.suggested_currency or "").strip().upper()
        factors = getattr(result, "factors", None) or {}
        query_text = str(factors.get("effective_work_description") or getattr(position, "description", ""))
        factor = work_rate_factor(result.suggested_unit, position.unit, query_text=query_text,
                                  candidate_text=result.suggested_description)
        # No honest conversion is a conflict whenever either side carries a
        # recognisable unit; two unknown units simply carry no signal.
        unit_conflict = factor is None and (
            normalize_unit(position.unit) is not None
            or normalize_unit(result.suggested_unit) is not None
        )
        currency_mismatch = bool(
            project_currency and suggestion_currency and suggestion_currency != project_currency
        )
        fx_rate: Decimal | None = None
        applied_rate = rate * factor if factor is not None else rate
        fx_pair: str | None = None
        if currency_mismatch and {suggestion_currency, project_currency} <= _FIXED_CURRENCY_RATES:
            # 1 EUR = 1.95583 BGN: an EUR position divides a BGN figure by the
            # peg, a BGN position multiplies an EUR figure by it.
            fx_rate = Decimal(1) / BGN_PER_EUR if project_currency == "EUR" else BGN_PER_EUR
            fx_pair = f"{suggestion_currency}->{project_currency}@{BGN_PER_EUR}"
            applied_rate = applied_rate * fx_rate
            currency_mismatch = False
        meta = dict(position.metadata_ or {})
        prior_offer = dict(meta.get("cost_match") or {})
        offer = {
            "run_id": str(run.id),
            "result_id": str(result.id),
            "cost_item_id": str(result.suggested_cost_item_id) if result.suggested_cost_item_id else None,
            "code": result.suggested_code,
            "description": result.suggested_description,
            "unit": result.suggested_unit,
            "suggested_rate": format(rate, "f"),
            "currency": suggestion_currency or result.suggested_currency,
            "currency_mismatch": currency_mismatch,
            "unit_mismatch": unit_conflict,
            "tier": result.tier,
            "confidence": format(result.confidence, "f"),
            "state": "pending_review",
        }
        # Cascade authority: an unpriced line is offered to every base, and
        # without this guard the LAST stage rewrites the offer regardless of
        # quality - an operator латекс row ends up masked by a sek "блажна
        # боя". A later stage may only replace the stored offer when it is
        # strictly better: a higher tier, or higher confidence at the same
        # tier; a unit/currency-conflicted offer ranks below every clean
        # one. On ties the earlier base keeps the line - that is the order
        # the operator declared by listing bases first.
        prior_conflict = bool(
            prior_offer.get("unit_mismatch") or prior_offer.get("currency_mismatch")
        )
        _TIER_RANK = {
            TIER_EXACT: 4,
            TIER_HIGH_CONFIDENCE: 3,
            TIER_NEEDS_REVIEW: 2,
            TIER_UNMATCHED: 0,
        }

        def _offer_key(tier: str, confidence: Any, conflicted: bool) -> tuple[int, float]:
            rank = -1 if conflicted else _TIER_RANK.get(tier, 1)
            try:
                conf = float(confidence or 0)
            except (TypeError, ValueError):
                conf = 0.0
            return (rank, conf)

        new_key = _offer_key(
            result.tier, result.confidence, unit_conflict or currency_mismatch
        )
        prior_key = _offer_key(
            str(prior_offer.get("tier") or ""),
            prior_offer.get("confidence"),
            prior_conflict,
        )
        keep_prior = (prior_offer.get('run_id') == str(run.id)
                      and bool(prior_offer) and new_key <= prior_key)
        human_basis = (position.price_basis or "") in _HUMAN_BASES
        if not keep_prior or human_basis:
            meta["cost_match"] = offer
            if factor is not None and factor != 1:
                meta["cost_match"]["unit_factor"] = format(factor, "f")
            if fx_rate is not None:
                meta["cost_match"]["fx"] = fx_pair
            if applied_rate != rate:
                meta["cost_match"]["applied_rate"] = format(applied_rate, "f")
        if human_basis:
            # Keep the ruling already recorded on this line: the fresh offer
            # stays on the result row, the metadata keeps the human's
            # decision fields, and rate/total/basis are left alone.
            prior = dict((position.metadata_ or {}).get("cost_match") or {})
            for key in ("decision", "state", "decided_code", "decided_currency", "applied_rate"):
                if key in prior:
                    meta["cost_match"][key] = prior[key]
            meta["cost_match"]["state"] = prior.get("state", "pending_review")
        position.metadata_ = meta
        if human_basis:
            return True
        if keep_prior:
            return position.price_basis in {BOQ_BASIS_EXACT, BOQ_BASIS_HIGH}
        if unit_conflict or currency_mismatch:
            if (position.price_basis or "") in _AUTO_WRITTEN_BASES:
                position.unit_rate = "0"
                position.total = "0"
            position.price_basis = BOQ_BASIS_UNIT if unit_conflict else BOQ_BASIS_FX
            if not keep_prior:
                position.confidence = format(
                    Decimal(result.confidence).quantize(_CONFIDENCE_PLACES), "f"
                )
            return False
        if result.tier == TIER_NEEDS_REVIEW:
            # A review-tier suggestion is a lead, not money. The offer stays
            # on the result and in ``metadata.cost_match`` for the reviewer,
            # but the position carries no rate until a person rules - a
            # flagged number must never reach a buyer-facing total.
            if (position.price_basis or "") in _AUTO_WRITTEN_BASES:
                position.unit_rate = "0"
                position.total = "0"
            position.price_basis = BOQ_BASIS_REVIEW
            if not keep_prior:
                position.confidence = format(
                    Decimal(result.confidence).quantize(_CONFIDENCE_PLACES), "f"
                )
            return False
        applied_rate = applied_rate.quantize(_CONFIDENCE_PLACES)
        position.unit_rate = format(applied_rate, "f")
        qty = _parse_rate(position.quantity)
        if qty is not None:
            position.total = str((qty * applied_rate).quantize(_CONFIDENCE_PLACES))
        position.price_basis = _TIER_BASIS.get(result.tier, BOQ_BASIS_REFERENCE)
        position.confidence = format(Decimal(result.confidence).quantize(_CONFIDENCE_PLACES), "f")
        return True

    async def _sync_position_from_decision(
        self,
        run: MatchRun,
        result: MatchResult,
        decision: MatchDecision,
    ) -> None:
        """Keep a BOQ-priced line in step with the ruling on its result.

        Only fires for runs raised by :meth:`run_boq_match` - their notes
        carry the submitted position ids in line order, so ``line_no`` maps
        straight back. A pasted-bill run has no positions to touch. A
        rejection clears the rate: keeping the offered number on a line a
        person just ruled wrong would resurrect the suggestion under their
        own authority.
        """
        position = await self._position_for_result(run, result)
        if position is None:
            return
        # A locked bill is frozen: the ruling stays recorded on the run but
        # the position's numbers do not move behind the lock.
        from app.modules.boq.models import BOQ

        boq = await self.session.get(BOQ, position.boq_id)
        if boq is not None and boq.is_locked:
            return
        meta = dict(position.metadata_ or {})
        cm = dict(meta.get("cost_match") or {})
        if decision.decision == DECISION_REJECTED:
            position.unit_rate = "0"
            position.total = "0"
            position.price_basis = BOQ_BASIS_REJECTED
            cm["decision"] = DECISION_REJECTED
            cm["state"] = "rejected"
        elif decision.decided_rate is not None:
            project_currency = ""
            if boq is not None:
                from app.modules.projects.models import Project

                project = await self.session.get(Project, boq.project_id)
                project_currency = (project.currency or "").strip().upper() if project else ""
            applied_rate = _decision_price(
                decision.decided_rate,
                decision.decided_unit,
                decision.decided_currency,
                target_unit=position.unit,
                project_currency=project_currency,
                query_text=str((getattr(result, "factors", None) or {}).get("effective_work_description")
                               or getattr(result, "source_description", "")),
                candidate_text=getattr(decision, "decided_description", ""),
            )
            position.unit_rate = format(applied_rate, "f")
            qty = _parse_rate(position.quantity)
            if qty is not None:
                position.total = str((qty * applied_rate).quantize(_CONFIDENCE_PLACES))
            position.price_basis = {
                DECISION_CONFIRMED: BOQ_BASIS_CONFIRMED,
                DECISION_OVERRIDDEN: BOQ_BASIS_OVERRIDDEN,
                DECISION_MANUAL: BOQ_BASIS_MANUAL,
            }.get(decision.decision, BOQ_BASIS_OVERRIDDEN)
            cm.update(
                {
                    "decision": decision.decision,
                    "decided_code": decision.decided_code,
                    "decided_currency": decision.decided_currency,
                    "state": decision.decision,
                }
            )
            if format(applied_rate, "f") != format(decision.decided_rate, "f"):
                cm["applied_rate"] = format(applied_rate, "f")
            if decision.decision == DECISION_MANUAL:
                await self._learn_manual_ruling(position, applied_rate, project_currency)
        else:
            return
        meta["cost_match"] = cm
        position.metadata_ = meta
        await self.session.flush()

    async def _learn_manual_ruling(
        self, position: Position, applied_rate: Decimal, project_currency: str
    ) -> None:
        """Persist a typed-in review price as an active operator corpus row.

        Same convention as ``update_position``'s learner: ``OPR-<position
        id>`` keyed so re-edits update rather than stack, and the learned
        rate is the one actually written to the line (post unit/FX
        normalisation) in project currency. Without this a manual ruling
        priced one position and taught the corpus nothing - re-importing the
        same bill asked all 250 questions again.
        """
        description = (position.description or "").strip()
        if applied_rate <= 0 or not description:
            return
        code = f"OPR-{position.id}"
        currency = project_currency or "EUR"
        existing = await self.session.scalar(
            select(CostItem).where(
                CostItem.code == code, CostItem.source == "operator_pricelist"
            )
        )
        meta: dict[str, Any] = {
            "learned_from": "manual_ruling",
            "boq_id": str(position.boq_id),
            "position_id": str(position.id),
        }
        from app.modules.cost_match.work_catalog import work_metadata
        from app.modules.cost_match.work_context import effective_work

        context = (position.metadata_ or {}).get('cost_match_work_context')
        meta['work_context'] = [s for s in context if isinstance(s, str)] if isinstance(context, list) else []
        meta['canonical_work'] = work_metadata(effective_work(description, meta['work_context']))
        if existing is not None:
            existing.description = description
            existing.unit = position.unit or ""
            existing.rate = format(applied_rate, "f")
            existing.currency = currency
            existing.is_active = True
            m = dict(existing.metadata_ or {})
            m.update(meta)
            existing.metadata_ = m
        else:
            self.session.add(
                CostItem(
                    code=code,
                    description=description,
                    descriptions={},
                    unit=position.unit or "",
                    rate=format(applied_rate, "f"),
                    currency=currency,
                    source="operator_pricelist",
                    classification={},
                    components=[],
                    tags=[],
                    region=None,
                    catalog_id=None,
                    is_active=True,
                    metadata_=meta,
                )
            )
        await self.session.flush()

    async def _position_for_result(self, run: MatchRun, result: MatchResult) -> Any | None:
        """The BOQ position a result was scored for, or ``None``.

        ``run_boq_match`` stores the submitted position ids on ``run.notes``
        in line order; anything else returns ``None`` and callers no-op.
        """
        try:
            meta = json.loads(run.notes or "")
        except (TypeError, ValueError):
            return None
        ids = meta.get("positions") if isinstance(meta, dict) else None
        if not ids or not (1 <= result.line_no <= len(ids)):
            return None
        try:
            position_id = uuid.UUID(ids[result.line_no - 1])
        except (TypeError, ValueError):
            return None
        from app.modules.boq.models import Position

        return await self.session.get(Position, position_id)

    # ── reading ─────────────────────────────────────────────────────────

    async def counts_for_runs(self, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, MatchRunCounts]:
        """Aggregated tier and decision counts for several runs at once."""
        grouped = await self.result_repo.tier_decision_counts(run_ids)
        return {run_id: _counts_from_groups(buckets) for run_id, buckets in grouped.items()}

    async def run_response(self, run: MatchRun) -> MatchRunResponse:
        """One run header with its counts filled in."""
        counts = await self.counts_for_runs([run.id])
        response = MatchRunResponse.model_validate(run)
        response.counts = counts.get(run.id, MatchRunCounts())
        return response

    def result_response(
        self,
        result: MatchResult,
        *,
        locale: str,
        web_estimate: WebEstimate | None = None,
    ) -> MatchResultResponse:
        """Shape one result for the API, rendered in ``locale``.

        The explanation is rendered here rather than stored: the reason codes
        are the durable record and the sentence is a view of them, so the same
        row reads in German for one reviewer and in Russian for the next.
        """
        response = MatchResultResponse.model_validate(result)
        query = (result.factors or {}).get('effective_work_description') or result.source_description
        if result.suggested_cost_item_id:
            response.factors = dict(response.factors)
            snapshot: dict[str, Any] = next((row for row in result.alternatives or []
                             if row.get('cost_item_id') == str(result.suggested_cost_item_id)), {})
            evidence = snapshot.get('evidence_description') or result.suggested_description
            response.factors['semantic_links'] = semantic_links(query, evidence)
        for candidate in response.alternatives:
            candidate.semantic_links = semantic_links(query, candidate.evidence_description or candidate.description)
        if web_estimate is not None:
            estimate = WebEstimateResponse.model_validate(web_estimate)
            # A record earns ``web_verified`` only when real pages were
            # fetched behind it; model-knowledge answers stay ``ai_estimate``.
            estimate.source = "web_verified" if web_estimate.sources else "ai_estimate"
            response.web_estimate = estimate
        codes = list(result.reason_codes or [])
        if codes:
            score = MatchScore(
                confidence=float(result.confidence),
                band=_band(result.confidence),
                factors={name: float(value) for name, value in (result.factors or {}).items()
                         if isinstance(value, int | float | Decimal)},
                reasons=codes,
            )
            response.explanation = explain(score, locale=locale)
        if result.hint_code:
            response.hint = no_match_hint(result.hint_code, locale=locale)
        return response

    # ── deciding ────────────────────────────────────────────────────────

    async def links_for_item(
        self, run: MatchRun, result: MatchResult, item_id: uuid.UUID,
    ) -> list[dict[str, str]]:
        active = await self.base_repo.get_active(
            item_id, cost_source=run.cost_source, region=run.region, catalog_id=run.catalog_id,
        )
        if active is None:
            raise LookupError(item_id)
        query = (result.factors or {}).get('effective_work_description') or result.source_description
        return semantic_links(query, _to_candidate(active, 'bg').text)

    async def record_decision(
        self,
        run: MatchRun,
        result: MatchResult,
        data: MatchDecisionCreate,
        *,
        decided_by: uuid.UUID | None,
        propagate_duplicates: bool = True,
    ) -> MatchDecision:
        """Record one person's ruling on one result.

        Appends to the ruling history rather than overwriting it, so changing
        your mind is visible instead of invisible, and moves the result's
        ``decision_state`` to match. The confidence and tier the machine was
        showing at that moment are frozen onto the ruling, because the point
        of the record is what the reviewer was looking at, not what the row
        says today.

        Args:
            run: The run the result belongs to, used to scope an override
                target to the same cost base and to refuse rulings on a
                closed run.
            result: The line being ruled on.
            data: The ruling.
            decided_by: The reviewer. Recorded on the row; a ruling without
                one is an ERROR from ``cost_match.decision_has_reviewer``.

        Returns:
            The persisted ruling.

        Raises:
            RunClosedError: The run's review has been closed.
            NoSuggestionToConfirmError: Confirming a line that got no
                suggestion.
            DecisionPayloadError: The payload contradicts the ruling.
            LookupError: The override target is not an active item of this
                run's cost base.
        """
        if run.status == RUN_STATUS_CLOSED:
            raise RunClosedError(run.id)

        snapshot: dict[str, Any] = {
            "decided_cost_item_id": None,
            "decided_code": "",
            "decided_description": "",
            "decided_unit": "",
            "decided_rate": None,
            "decided_currency": "",
        }

        if data.decision == DECISION_CONFIRMED:
            if data.cost_item_id is not None and data.cost_item_id != result.suggested_cost_item_id:
                raise DecisionPayloadError(
                    "a confirmation adopts the suggestion as it stands; use 'overridden' to choose another item"
                )
            if result.suggested_cost_item_id is None:
                raise NoSuggestionToConfirmError(result.id)
            pooled = "pooled_median" in (result.reason_codes or []) or (
                (result.factors or {}).get("pool_median") is not None
            )
            if pooled:
                # The suggested rate is a synthetic pool median - no single
                # corpus row ever carried it, so the ruling must not pair
                # one member's identity with a price that member never had.
                snapshot.update(
                    decided_cost_item_id=None,
                    decided_code="",
                    decided_description=result.suggested_description,
                    decided_unit=result.suggested_unit,
                    decided_rate=result.suggested_rate,
                    decided_currency=result.suggested_currency,
                )
            else:
                snapshot.update(
                    decided_cost_item_id=result.suggested_cost_item_id,
                    decided_code=result.suggested_code,
                    decided_description=result.suggested_description,
                    decided_unit=result.suggested_unit,
                    decided_rate=result.suggested_rate,
                    decided_currency=result.suggested_currency,
                )
        elif data.decision == DECISION_OVERRIDDEN:
            if data.cost_item_id is None:
                raise DecisionPayloadError("an override must name the cost item to adopt")
            item = await self.base_repo.get_active(
                data.cost_item_id,
                cost_source=run.cost_source,
                region=run.region,
                catalog_id=run.catalog_id,
            )
            if item is None:
                raise LookupError("cost item is not an active entry of this run's cost base")
            snapshot.update(
                decided_cost_item_id=item.id,
                decided_code=item.code,
                decided_description=_localized_description(item, run.source_locale),
                decided_unit=item.unit,
                decided_rate=_parse_rate(item.rate),
                decided_currency=item.currency,
            )
        elif data.decision == DECISION_MANUAL:
            if data.rate is None or data.rate <= 0:
                raise DecisionPayloadError("a manual ruling must carry a positive rate")
            if data.cost_item_id is not None:
                raise DecisionPayloadError("a manual ruling names no cost item - that is what makes it manual")
            # The price stands on the reviewer, so the snapshot describes the
            # line itself: no borrowed corpus code, no borrowed description.
            snapshot.update(
                decided_description=result.source_description,
                decided_unit=result.source_unit,
                decided_rate=data.rate,
                decided_currency=(data.currency or "EUR").strip().upper() or "EUR",
            )
        elif data.cost_item_id is not None:
            raise DecisionPayloadError("a rejection adopts no cost item, so it must not name one")
        if data.decision != DECISION_MANUAL and data.rate is not None:
            raise DecisionPayloadError("a rate is only meaningful on a manual ruling")

        chosen_links: list[tuple[dict[str, str], str, CostItem]] = []
        if data.semantic_choices:
            if data.decision == DECISION_MANUAL:
                raise DecisionPayloadError('a manually supplied price has no corpus work link to verify')
            target_id = (data.cost_item_id if data.decision == DECISION_OVERRIDDEN
                         else result.suggested_cost_item_id)
            query = (result.factors or {}).get('effective_work_description') or result.source_description
            items: dict[uuid.UUID, CostItem] = {}
            seen: set[tuple[uuid.UUID, str]] = set()
            for choice in data.semantic_choices:
                item_id = choice.cost_item_id or target_id
                if item_id is None or item_id not in {target_id, result.suggested_cost_item_id}:
                    raise DecisionPayloadError('semantic link belongs to a different quotation')
                if item_id not in items:
                    active = await self.base_repo.get_active(
                        item_id, cost_source=run.cost_source, region=run.region,
                        catalog_id=run.catalog_id,
                    )
                    if active is None:
                        raise LookupError('semantic feedback needs an active quotation in this cost base')
                    items[item_id] = active
                evidence = _to_candidate(items[item_id], 'bg').text
                by_id = {link['id']: link for link in semantic_links(query, evidence)}
                if (item_id, choice.link_id) in seen or choice.link_id not in by_id:
                    raise DecisionPayloadError('unknown or repeated semantic link for this quotation')
                seen.add((item_id, choice.link_id))
                chosen_links.append((by_id[choice.link_id], choice.verdict, items[item_id]))
            if (data.decision != DECISION_REJECTED and any(
                verdict == 'different' and item.id == target_id
                for _, verdict, item in chosen_links
            )):
                raise DecisionPayloadError('a quotation marked as different cannot be confirmed')

        if data.decision != DECISION_REJECTED:
            from app.modules.projects.models import Project

            position = await self._position_for_result(run, result)
            project = await self.session.get(Project, result.project_id)
            _decision_price(
                snapshot["decided_rate"],
                snapshot["decided_unit"],
                snapshot["decided_currency"],
                target_unit=position.unit if position is not None else result.source_unit,
                project_currency=project.currency if project is not None else "",
                query_text=str((getattr(result, "factors", None) or {}).get("effective_work_description")
                               or result.source_description),
                candidate_text=snapshot["decided_description"],
            )

        decision = MatchDecision(
            result_id=result.id,
            run_id=result.run_id,
            seq=await self.decision_repo.next_seq(result.id),
            decision=data.decision,
            tier_at_decision=result.tier,
            confidence_at_decision=result.confidence,
            decided_by=decided_by,
            note='; '.join(filter(None, [data.note, *(
                f"[{item.code}] {link['label']}: {link['query']} ↔ {link['candidate']} = "
                f"{'същото' if verdict == 'same' else 'различно'}"
                for link, verdict, item in chosen_links
            )])) or None,
            **snapshot,
        )
        await self.decision_repo.create(decision)
        await self.session.flush()
        await self._record_pattern(run, result, decision, chosen_links)
        result.decision_state = data.decision
        await self.session.flush()
        await self._sync_position_from_decision(run, result, decision)
        if propagate_duplicates:
            await self._propagate_to_duplicates(run, result, decision, decided_by)

        event_bus.publish_detached(
            events.MATCH_REVIEWED,
            {
                "result_id": str(result.id),
                "run_id": str(result.run_id),
                "project_id": str(result.project_id),
                "decision": data.decision,
                "decided_by": str(decided_by) if decided_by else None,
                "tenant_id": str(run.tenant_id) if run.tenant_id else None,
            },
            source_module=events.SOURCE_MODULE,
        )
        return decision

    async def _propagate_to_duplicates(
        self,
        run: MatchRun,
        result: MatchResult,
        decision: MatchDecision,
        decided_by: uuid.UUID | None,
    ) -> int:
        """Carry one ruling to identical still-pending lines of the same run.

        A bill repeats rows - the same description and unit appearing forty
        times is forty copies of one question, and a person answering it once
        has answered it for all of them. The propagated ruling is recorded
        with the same adopted item/rate (or the same rejection) and marked in
        its note, so the audit trail says which line a human actually looked
        at and which ones inherited the answer. Each duplicate still gets its
        own decision row, its own pattern, and its own position sync - the
        propagation is a convenience, not a silent bypass of the decision
        flow.
        """
        key_desc = normalize_text(result.source_description)
        key_unit = result.source_unit or ""
        if not key_desc:
            return 0
        siblings = [
            r
            for r in await self.result_repo.list_all_for_run(result.run_id)
            if r.id != result.id
            and r.decision_state == DECISION_PENDING
            and normalize_text(r.source_description) == key_desc
            and (r.source_unit or "") == key_unit
        ]
        for dup in siblings:
            dup_decision = MatchDecision(
                result_id=dup.id,
                run_id=dup.run_id,
                seq=await self.decision_repo.next_seq(dup.id),
                decision=decision.decision,
                tier_at_decision=dup.tier,
                confidence_at_decision=dup.confidence,
                decided_cost_item_id=decision.decided_cost_item_id,
                decided_code=decision.decided_code,
                decided_description=decision.decided_description,
                decided_unit=decision.decided_unit,
                decided_rate=decision.decided_rate,
                decided_currency=decision.decided_currency,
                decided_by=decided_by,
                note=f"propagated from line {result.line_no}",
            )
            await self.decision_repo.create(dup_decision)
            await self.session.flush()
            await self._record_pattern(run, dup, dup_decision)
            dup.decision_state = decision.decision
            await self.session.flush()
            await self._sync_position_from_decision(run, dup, dup_decision)
        return len(siblings)

    # ── web-verify: AI-extracted market estimates ───────────────────────

    async def estimates_for_results(
        self, results: list[MatchResult]
    ) -> dict[str, WebEstimate]:
        """Signature -> stored estimate for a page of results.

        One query per page: estimates dedupe on signature, so lines sharing
        a description share one record regardless of which run produced it.
        """
        sigs = {
            estimate_signature(r.source_description, r.source_unit)
            for r in results
            if (r.source_description or "").strip()
        }
        if not sigs:
            return {}
        rows = (
            await self.session.execute(
                select(WebEstimate)
                .where(WebEstimate.signature.in_(sigs))
                .order_by(WebEstimate.created_at.desc())
            )
        ).scalars().all()
        by_sig: dict[str, WebEstimate] = {}
        for row in rows:
            by_sig.setdefault(row.signature, row)
        return by_sig

    async def run_web_verify(
        self,
        run: MatchRun,
        *,
        scope: str = "queue",
        on_progress: Any | None = None,
    ) -> dict[str, Any]:
        """Extract AI market estimates for a run's lines. Advisory only.

        Each unique line (normalized description + unit) costs one LLM call;
        duplicates reuse the stored record, and lines the base has already
        verified are counted as reused rather than re-fetched. ``scope="queue"``
        covers the review queue; ``"all"`` re-checks priced lines too - the
        same estimate doubles as a discrepancy flag against their current rate.

        Nothing here writes money to a position. The records land on
        ``oe_cost_match_web_estimate`` for the reviewer to rule on.
        """
        from app.config import get_settings

        settings = get_settings()
        provider = settings.web_verify_provider
        api_key = (
            settings.gemini_api_key or settings.web_verify_key
            if provider == "gemini"
            else settings.web_verify_key
        )
        if provider == "gemini":
            configured = bool(api_key and settings.web_verify_gemini_model)
        else:
            configured = bool(settings.web_verify_url and settings.web_verify_model)
        if not configured:
            raise WebVerifyError(
                "web-verify is not configured (OE_WEB_VERIFY_* for the chosen provider)"
            )

        results = await self.result_repo.list_all_for_run(run.id)
        if scope == "queue":
            results = [
                r
                for r in results
                if r.decision_state == DECISION_PENDING and r.tier in QUEUE_TIERS
            ]
        lines = [r for r in results if (r.source_description or "").strip()]

        # One signature = one extraction. Blank descriptions were filtered
        # above; everything else groups onto its signature.
        sig_to_result: dict[str, MatchResult] = {}
        for r in lines:
            sig_to_result.setdefault(
                estimate_signature(r.source_description, r.source_unit), r
            )
        unique_sigs = list(sig_to_result)
        existing = await self.estimates_for_results(lines)

        todo = [s for s in unique_sigs if s not in existing]
        totals: dict[str, Any] = {
            "run_id": str(run.id),
            "lines_seen": len(lines),
            "unique_lines": len(unique_sigs),
            "estimates_written": 0,
            "estimates_reused": len(unique_sigs) - len(todo),
            "failed": 0,
        }
        if not todo:
            if on_progress:
                on_progress(len(unique_sigs), len(unique_sigs))
            return totals

        import asyncio

        import httpx

        # Sequential for the shared self-hosted kimi endpoint - parallel
        # calls just pile up in its queue. Gemini's API is fine with a few
        # in flight, so the concurrency follows the provider.
        semaphore = asyncio.Semaphore(8 if provider == "gemini" else 1)
        # One AsyncSession is not safe for concurrent writes; the wider
        # model-call semaphore must not widen the commit path too.
        db_lock = asyncio.Lock()
        # If a Gemini call falls back to the kimi endpoint, that shared
        # NVL72 queue still gets exactly one request at a time.
        kimi_gate = asyncio.Semaphore(1)
        done = 0
        if on_progress:
            on_progress(0, len(unique_sigs))

        async def _one(client: httpx.AsyncClient, sig: str) -> None:
            nonlocal done
            result = sig_to_result[sig]
            try:
                async with semaphore:
                    fields = await fetch_estimate(
                        result.source_description,
                        result.source_unit,
                        url=settings.web_verify_url,
                        api_key=api_key,
                        model=settings.web_verify_model,
                        client=client,
                        search_provider=settings.web_search_provider,
                        search_key=settings.web_search_key,
                        provider=provider,
                        gemini_model=settings.web_verify_gemini_model,
                        reserp_key=settings.reserp_api_key,
                        fallback_lock=kimi_gate,
                        openai_key=settings.web_verify_key,
                    )
                    estimate = WebEstimate(
                        signature=sig,
                        result_id=result.id,
                        run_id=run.id,
                        project_id=result.project_id,
                        description=result.source_description,
                        unit=result.source_unit,
                        model=(
                            settings.web_verify_gemini_model
                            if provider == "gemini"
                            else settings.web_verify_model
                        ),
                        status=WEB_ESTIMATE_SUGGESTED,
                        **fields,
                    )
                async with db_lock:
                    self.session.add(estimate)
                    # Commit per line: a several-hundred-line job runs for
                    # hours, and holding every estimate uncommitted until the
                    # end would lose all of them if the process dies mid-run.
                    await self.session.commit()
                    totals["estimates_written"] += 1
            except WebVerifyError as exc:
                totals["failed"] += 1
                logger.warning(
                    "web-verify failed for line %s (%s): %s",
                    result.line_no,
                    sig[:60],
                    exc,
                )
            except Exception:  # noqa: BLE001 - one bad line must not sink the job
                totals["failed"] += 1
                logger.exception("web-verify crashed on line %s", result.line_no)
            finally:
                done += 1
                if on_progress:
                    on_progress(len(unique_sigs) - len(todo) + done, len(unique_sigs))

        async with httpx.AsyncClient() as client:
            await asyncio.gather(*(_one(client, s) for s in todo))
        return totals

    async def update_run(self, run: MatchRun, data: MatchRunUpdate) -> MatchRun:
        """Patch a run's reviewer-editable metadata.

        Only the fields the caller actually sent are touched, so an omitted
        field is left alone. ``notes`` is nullable and can be cleared with an
        explicit null; the rest are NOT NULL and an explicit null is simply
        not applied rather than written and failing at flush.
        """
        fields = data.model_dump(exclude_unset=True)
        for name, value in fields.items():
            if value is None and name != "notes":
                continue
            setattr(run, name, value)
        await self.session.flush()
        return run

    # ── bulk confirmation ────────────────────────────────────────────────

    async def confirm_boq_positions(
        self,
        boq: Any,
        *,
        position_ids: list[uuid.UUID] | None,
        decided_by: uuid.UUID | None,
        learn_to_corpus: bool = True,
    ) -> dict[str, int]:
        """Bulk-confirm pending suggestions on a bill's positions.

        The grid's "confirm selected / confirm all" path. A rate the matcher
        wrote stays a suggestion until a person rules on it; every row here
        goes through :meth:`record_decision`, so bulk rulings land in the
        same append-only history with the same reviewer attribution as the
        one-by-one queue. A position may appear in several runs - it is
        confirmed once, against the newest run that scored it.

        ``position_ids=None`` targets every still-pending result on the
        bill's runs; a list scopes rulings to the selected rows. Rows that
        cannot take a ruling - already decided, no suggestion to confirm, no
        result at all - are counted as skipped, never invented.
        """
        from app.modules.projects.models import Project

        project = await self.session.get(Project, boq.project_id)
        runs = await self.run_repo.list_for_boq(boq.project_id, boq.id)
        wanted = set(position_ids) if position_ids is not None else None
        confirmed = skipped = learned = 0
        done_positions: set[uuid.UUID] = set()
        newest_positions: set[uuid.UUID] = set()

        for run in runs:
            try:
                meta = json.loads(run.notes or "")
            except (TypeError, ValueError):
                continue
            ids = meta.get("positions") if isinstance(meta, dict) else None
            if not ids:
                continue
            pos_by_line: dict[int, uuid.UUID] = {}
            for i, raw in enumerate(ids):
                try:
                    pos_by_line[i + 1] = uuid.UUID(raw)
                except (TypeError, ValueError):
                    continue
            results = await self.result_repo.list_all_for_run(run.id)
            result_by_pos = {
                pos_by_line[r.line_no]: r for r in results
                if r.line_no in pos_by_line and pos_by_line[r.line_no] not in newest_positions
            }
            newest_positions.update(result_by_pos)
            if run.status != RUN_STATUS_MATCHED:
                continue
            if wanted is None:
                targets = {
                    pid
                    for pid, r in result_by_pos.items()
                    if r.decision_state == DECISION_PENDING
                }
            else:
                targets = {pid for pid in wanted if pid in result_by_pos}
            for pos_id in sorted(targets, key=str):
                if pos_id in done_positions:
                    continue
                result = result_by_pos.get(pos_id)
                if (
                    result is None
                    or result.decision_state != DECISION_PENDING
                    or result.suggested_cost_item_id is None
                ):
                    skipped += 1
                    done_positions.add(pos_id)
                    continue
                # Capture the basis and rate BEFORE the ruling writes
                # corpus_confirmed over them - the learn gate needs the
                # operator's declared state, not the post-confirm stamp.
                position = await self._position_for_result(run, result)
                declared_basis = (
                    (position.price_basis or "") if position is not None else ""
                )
                declared_rate = (
                    _parse_rate(position.unit_rate) if position is not None else None
                )
                try:
                    await self.record_decision(
                        run,
                        result,
                        MatchDecisionCreate(decision=DECISION_CONFIRMED),
                        decided_by=decided_by,
                        propagate_duplicates=False,
                    )
                except (
                    RunClosedError,
                    NoSuggestionToConfirmError,
                    DecisionPayloadError,
                    LookupError,
                ):
                    skipped += 1
                    done_positions.add(pos_id)
                    continue
                confirmed += 1
                done_positions.add(pos_id)
                if learn_to_corpus:
                    if position is not None and await self._learn_confirmed_price(
                        boq,
                        project,
                        position,
                        declared_basis=declared_basis,
                        declared_rate=declared_rate,
                    ):
                        learned += 1
        # Wanted ids with no result on any run count once as skipped.
        if wanted is not None:
            skipped += len(wanted - done_positions)
        return {"confirmed": confirmed, "skipped": skipped, "learned": learned}

    async def _learn_confirmed_price(
        self,
        boq: Any,
        project: Any,
        position: Any,
        *,
        declared_basis: str = "",
        declared_rate: Decimal | None = None,
    ) -> bool:
        """Persist a confirmed position price as a corpus item.

        Learning is gated on declared human evidence: the position's
        ``price_basis`` must be one of the operator basis values (invoice,
        quotation, price list, contract rate, norm, historic, judgement). A
        ``corpus_*`` basis means the number came from the matcher - a plain
        confirm only rubber-stamps a suggestion, and learning it teaches the
        corpus to repeat its own guesses as ``exact`` matches. The rate must
        also be the one the operator declared: if the ruling replaced it with
        a suggestion or a synthetic pool median, nothing authored remains to
        learn. Keyed by ``CONF-<position id>`` so a second learning updates
        rather than stacks, and an update never reactivates a quarantined row.
        """
        basis = (declared_basis or (position.price_basis or "")).strip()
        if basis not in _OPERATOR_PRICE_BASES:
            return False
        rate = _parse_rate(position.unit_rate)
        description = (position.description or "").strip()
        if rate is None or rate <= 0 or not description:
            return False
        if declared_rate is None or rate != declared_rate:
            return False
        code = f"CONF-{position.id}"
        currency = (project.currency or "").strip().upper() if project else ""
        region = project.region if project else None
        from app.modules.cost_match.work_catalog import work_metadata
        from app.modules.cost_match.work_context import effective_work

        context = (position.metadata_ or {}).get('cost_match_work_context')
        parents = [s for s in context if isinstance(s, str)] if isinstance(context, list) else []
        meta = {
            "learned_from": "operator_declared",
            "price_basis": basis,
            "boq_id": str(boq.id),
            "position_id": str(position.id),
            "work_context": parents,
            "canonical_work": work_metadata(effective_work(description, parents)),
        }
        existing = await self.session.scalar(
            select(CostItem).where(
                CostItem.code == code, CostItem.source == "estimate_confirmed"
            )
        )
        if existing is not None:
            existing.description = description
            existing.unit = position.unit or ""
            existing.rate = format(rate, "f")
            existing.currency = currency
            existing.region = region
            updated_meta = dict(existing.metadata_ or {})
            updated_meta.update(meta)
            existing.metadata_ = updated_meta
        else:
            self.session.add(
                CostItem(
                    code=code,
                    description=description,
                    descriptions={},
                    unit=position.unit or "",
                    rate=format(rate, "f"),
                    currency=currency,
                    source="estimate_confirmed",
                    classification={},
                    components=[],
                    tags=[],
                    region=region,
                    catalog_id=None,
                    metadata_=meta,
                )
            )
        await self.session.flush()
        return True

    # ── validation ──────────────────────────────────────────────────────

    def result_payload(self, result: MatchResult) -> dict[str, Any]:
        """Plain-dict view of one result for the rule engine.

        Money, quantities and confidence go across as decimal strings so no
        rule ever sees a float, and the ruling in force is flattened onto the
        payload because that is what every result-scope rule is actually
        about.
        """
        payload: dict[str, Any] = {
            "id": str(result.id),
            "run_id": str(result.run_id),
            "line_no": result.line_no,
            "source_ref": result.source_ref,
            "source_description": result.source_description,
            "source_unit": result.source_unit,
            "source_quantity": None if result.source_quantity is None else format(result.source_quantity, "f"),
            "tier": result.tier,
            "confidence": format(result.confidence, "f"),
            "tie": result.tie,
            "hint_code": result.hint_code,
            "suggested_cost_item_id": (str(result.suggested_cost_item_id) if result.suggested_cost_item_id else None),
            "suggested_code": result.suggested_code,
            "suggested_unit": result.suggested_unit,
            "suggested_rate": None if result.suggested_rate is None else format(result.suggested_rate, "f"),
            "suggested_currency": result.suggested_currency,
            "decision_state": result.decision_state,
            "alternative_count": len(result.alternatives or []),
        }
        decision = result.current_decision
        if decision is not None:
            payload.update(
                {
                    "decision_seq": decision.seq,
                    "decided_by": str(decision.decided_by) if decision.decided_by else None,
                    "decided_cost_item_id": (
                        str(decision.decided_cost_item_id) if decision.decided_cost_item_id else None
                    ),
                    "decided_code": decision.decided_code,
                    "decided_description": decision.decided_description,
                    "decided_unit": decision.decided_unit,
                    "decided_rate": None if decision.decided_rate is None else format(decision.decided_rate, "f"),
                    "decided_currency": decision.decided_currency,
                    "confidence_at_decision": format(decision.confidence_at_decision, "f"),
                    "note": decision.note,
                }
            )
        return payload

    async def validate_result(
        self,
        result: MatchResult,
        *,
        locale: str = "",
    ) -> CostMatchValidationReport:
        """Run the result-scope ``cost_match`` rules over one line."""
        return await evaluate_result(
            {"result": self.result_payload(result)},
            project_id=str(result.project_id),
            locale=locale,
        )

    async def validate_run(
        self,
        run: MatchRun,
        *,
        locale: str = "",
    ) -> CostMatchValidationReport:
        """Run both scopes over a whole run and merge the reports.

        The run-scope rules see what no single line can (currency spread,
        the same scope priced two ways, how much queue is left); the
        result-scope rules are applied to every line and folded in, because a
        bill is only as sound as its worst line.
        """
        results = await self.result_repo.list_all_for_run(run.id)
        payloads = [self.result_payload(r) for r in results]
        reports = [
            await evaluate_run(
                {
                    "run": {
                        "id": str(run.id),
                        "name": run.name,
                        "status": run.status,
                        "item_count": run.item_count,
                        "cost_source": run.cost_source,
                        "region": run.region,
                    },
                    "results": payloads,
                },
                project_id=str(run.project_id),
                locale=locale,
            )
        ]
        for payload in payloads:
            reports.append(
                await evaluate_result(
                    {"result": payload},
                    project_id=str(run.project_id),
                    locale=locale,
                )
            )
        return merge_reports(reports, target_type="cost_match_run", target_id=str(run.id))


# ── module-level count helpers ──────────────────────────────────────────────


def _parse_rate(raw: str | None) -> Decimal | None:
    """Parse a cost base's Decimal-as-string rate, or ``None`` if unusable.

    ``None`` rather than zero on failure: a missing rate and a rate of zero
    are different findings and the ``cost_match.rate_present`` rule reports
    them differently.
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _hint_code(hint: str | None, description: str, candidates: list[Candidate]) -> str:
    """Recover the matcher's hint code from the fact that it produced a hint.

    The matcher renders hints into the reader's language, and a rendered
    sentence is the one thing that must not be persisted. The three cases it
    hints about are distinguishable from the inputs, so the code is derived
    from those and the sentence is re-rendered per reader on the way out.

    The empty-query test asks the matcher's own question, not whether the text
    looks blank. A line of nothing but stop words is not blank and does still
    retrieve rows, but it carries no term to score on, so the matcher treats it
    as empty. Testing ``strip()`` here would label it a poor match and send the
    reviewer looking for a better candidate that cannot exist.
    """
    if not hint:
        return ""
    if not canonical_tokens(description or ""):
        return "empty_query"
    if not candidates:
        return "no_candidates"
    return "no_good_match"


def _counts_from_rows(rows: list[MatchResult]) -> MatchRunCounts:
    """Aggregate freshly scored rows without a round trip to the database."""
    buckets: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (row.tier, row.decision_state)
        buckets[key] = buckets.get(key, 0) + 1
    return _counts_from_groups(buckets)


def _counts_from_groups(buckets: dict[tuple[str, str], int]) -> MatchRunCounts:
    """Fold a ``(tier, decision_state) -> count`` grouping into the response.

    Every figure the UI shows is derived here from the rows themselves, which
    is why no count is stored on the run: a counter and its rows drift, and a
    queue badge that disagrees with the queue destroys trust in the feature.
    """
    counts = MatchRunCounts()
    tier_field = {
        TIER_EXACT: "exact",
        TIER_HIGH_CONFIDENCE: "high_confidence",
        TIER_NEEDS_REVIEW: "needs_review",
        TIER_UNMATCHED: "unmatched",
    }
    state_field = {
        DECISION_PENDING: "pending",
        DECISION_CONFIRMED: "confirmed",
        DECISION_OVERRIDDEN: "overridden",
        DECISION_REJECTED: "rejected",
        DECISION_MANUAL: "manual",
    }
    for (tier, state), count in buckets.items():
        counts.total += count
        if tier in tier_field:
            setattr(counts, tier_field[tier], getattr(counts, tier_field[tier]) + count)
        if state in state_field:
            setattr(counts, state_field[state], getattr(counts, state_field[state]) + count)
        if tier in QUEUE_TIERS and state == DECISION_PENDING:
            counts.queue_length += count
    return counts
