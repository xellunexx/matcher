# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Service-level behaviour of cost matching against a real database.

Covers the things only rows can show: what retrieval actually pulls out of a
cost base, which tier each scored line lands in, that identical lines are
scored once and not once each, that a ruling is an append-only record rather
than a flag, and that the relationship loading strategies declared on the
models behave the way the loading policy says they must.

The four seeded cost items are in ``conftest.py``. Every expected tier below is
derived by hand from those descriptions and the matcher's published formula, so
a change in scoring surfaces here rather than in production.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import selectinload

from app.database import async_session_factory
from app.modules.cost_match.matcher import REVIEW_CONFIDENCE
from app.modules.cost_match.models import (
    DECISION_CONFIRMED,
    DECISION_OVERRIDDEN,
    DECISION_PENDING,
    DECISION_REJECTED,
    RUN_STATUS_CLOSED,
    TIER_EXACT,
    TIER_HIGH_CONFIDENCE,
    TIER_NEEDS_REVIEW,
    TIER_UNMATCHED,
    MatchResult,
    MatchRun,
)
from app.modules.cost_match.repository import CostBaseRepository, retrieval_terms
from app.modules.cost_match.schemas import (
    MatchDecisionCreate,
    MatchLineInput,
    MatchRunCreate,
)
from app.modules.cost_match.service import (
    CostMatchService,
    DecisionPayloadError,
    NoSuggestionToConfirmError,
    RunClosedError,
)
from tests.modules.cost_match.conftest import TEST_REGION, TEST_SOURCE


@pytest_asyncio.fixture
async def session():
    """A fresh session per test, so identity-map state never leaks."""
    async with async_session_factory() as db:
        yield db
        await db.rollback()


def _line(description: str, unit: str = "", quantity: str | None = None, source_ref: str = "") -> MatchLineInput:
    return MatchLineInput(
        description=description,
        unit=unit,
        quantity=None if quantity is None else Decimal(quantity),
        source_ref=source_ref,
    )


def _payload(project_id: str, lines: list[MatchLineInput], **overrides: object) -> MatchRunCreate:
    data: dict[str, object] = {
        "project_id": uuid.UUID(project_id),
        "name": "Subcontractor bill",
        "source_label": "Foreign sub",
        "source_locale": "en",
        "cost_source": TEST_SOURCE,
        "region": TEST_REGION,
        "candidate_limit": 40,
        "lines": lines,
    }
    data.update(overrides)
    return MatchRunCreate(**data)


async def _results(service: CostMatchService, run: MatchRun) -> list[MatchResult]:
    return await service.result_repo.list_all_for_run(run.id)


# ── retrieval ───────────────────────────────────────────────────────────────


class TestRetrieval:
    def test_terms_carry_both_the_words_and_the_concepts(self) -> None:
        """Retrieval has to ask the database the question the scorer will ask.

        A German compound contributes its own spelling (so an accented or
        native-language row is reachable) and the matcher's English concept
        tokens (so an English base row is reachable from a German line).
        """
        terms = retrieval_terms("Stahlbetonwand C30/37")
        assert "Stahlbetonwand" in terms
        assert "concrete" in terms
        assert "wall" in terms
        # Two-character noise never becomes a retrieval term.
        assert "37" not in terms

    def test_terms_are_capped_and_deduplicated(self) -> None:
        terms = retrieval_terms("concrete concrete concrete " + " ".join(f"word{i}" for i in range(40)))
        assert len(terms) <= 16
        assert len(terms) == len(set(terms))

    async def test_a_german_line_reaches_an_english_base_row(self, cost_base, session) -> None:
        repo = CostBaseRepository(session)
        items = await repo.find_candidates(
            "Bewehrungsstahl",
            cost_source=TEST_SOURCE,
            region=TEST_REGION,
            limit=40,
        )
        assert "CM-REBAR" in {item.code for item in items}

    async def test_retrieval_is_scoped_to_the_run_cost_base(self, cost_base, session) -> None:
        """A base the run is not pinned to must never contribute a candidate."""
        repo = CostBaseRepository(session)
        items = await repo.find_candidates(
            "Reinforced concrete wall",
            cost_source=TEST_SOURCE,
            region="OE_REGION_THAT_DOES_NOT_EXIST",
            limit=40,
        )
        assert items == []

    async def test_a_blank_query_returns_nothing_rather_than_the_catalogue(self, cost_base, session) -> None:
        repo = CostBaseRepository(session)
        assert await repo.find_candidates("", cost_source=TEST_SOURCE, region=TEST_REGION) == []


# ── tiers ───────────────────────────────────────────────────────────────────


class TestTiers:
    async def test_word_for_word_match_is_exact(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_EXACT
        assert result.confidence == Decimal("1.0000")
        assert result.suggested_code == "CM-C30-WALL"
        assert result.suggested_rate == Decimal("185.0000")
        assert result.suggested_currency == "EUR"
        assert "exact_match" in result.reason_codes
        # Nothing is applied by matching alone.
        assert result.decision_state == DECISION_PENDING

    async def test_cross_language_synonym_is_high_confidence(self, cost_base, project_id, session) -> None:
        """A German compound maps onto an English row through shared concepts."""
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Bewehrungsstahl", "kg", "3200")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_HIGH_CONFIDENCE
        assert result.suggested_code == "CM-REBAR"
        assert result.confidence >= Decimal("0.7500")

    async def test_partial_concept_overlap_is_confident(self, cost_base, project_id, session) -> None:
        """Compound splitting turns one German word into real evidence.

        "Stahlbetonwand" decomposes to steel + concrete + wall; the English
        base row answers two of those content concepts and contradicts
        nothing. Under evidence scoring two shared nouns plus no dissent is
        a confident match - the line's silence on the concrete class is not
        a conflict, it just isn't corroboration.
        """
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_HIGH_CONFIDENCE
        assert result.suggested_code == "CM-C30-WALL"
        assert result.confidence >= Decimal("0.7500")

    async def test_the_run_locale_picks_the_description_that_is_scored(self, cost_base, project_id, session) -> None:
        """The same German line scores far better against the German base text.

        This is what the per-locale descriptions on a cost item are for, and
        it is why the locale is pinned on the run rather than passed per read.
        """
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")], source_locale="de"))
        (result,) = await _results(service, run)
        assert result.tier == TIER_HIGH_CONFIDENCE
        assert result.suggested_description == "Stahlbetonwand C30/37"

    async def test_an_exact_text_match_in_the_wrong_dimension_is_not_exact(
        self, cost_base, project_id, session
    ) -> None:
        """The unit penalty is what stops a trap from wearing a green badge.

        The description is word-for-word identical, so the text score is 1.0;
        the line is measured in square metres and the item is priced per cubic
        metre, so the match must not present as settled.
        """
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m2", "80")]))
        (result,) = await _results(service, run)
        assert result.tier != TIER_EXACT
        assert result.tier == TIER_NEEDS_REVIEW
        assert "unit_mismatch" in result.reason_codes

    async def test_nothing_in_the_base_leaves_the_line_unmatched(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Bituminous shingle underlay felt", "m2", "310")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_UNMATCHED
        assert result.suggested_cost_item_id is None
        assert result.hint_code == "no_candidates"

    async def test_a_blank_line_is_unmatched_with_an_empty_query_hint(self, cost_base, project_id, session) -> None:
        """Blank rows really are pasted, and they must survive to the reviewer."""
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("   ")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_UNMATCHED
        assert result.hint_code == "empty_query"

    async def test_a_stop_word_line_is_an_empty_query_not_a_poor_match(self, cost_base, project_id, session) -> None:
        """A continuation row is not blank, but it carries nothing to score on.

        Bills are full of rows like this - a preamble, a "ditto" continuation,
        a stray fragment of the sentence above. The text is not empty and it
        does reach rows in the base, because these short words are substrings
        of real descriptions. It still yields no concept token, so the matcher
        calls it an empty query. The stored hint has to say the same thing:
        telling the reviewer to look for a better candidate would send them
        after one that cannot exist.
        """
        repo = CostBaseRepository(session)
        reached = await repo.find_candidates("for the", cost_source=TEST_SOURCE, region=TEST_REGION, limit=40)
        # Stop words no longer reach retrieval: a line reduced to zero
        # meaning-bearing terms is a blank query, and retrieving rows on the
        # strength of "for" or "the" would be a suggestion with no evidence
        # behind it - the same reason find_candidates refuses blank text.
        assert not reached, "a stop-word-only line must not retrieve on substring luck"

        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("for the", "m2", "15")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_UNMATCHED
        assert result.hint_code == "empty_query"

    async def test_a_below_floor_suggestion_is_kept_for_the_reviewer(self, cost_base, project_id, session) -> None:
        """Unmatched does not mean empty-handed, and that is load bearing.

        A pump-hire line shares one word with the wall item and nothing else,
        so the score lands under the review floor and the line is unmatched.
        The suggestion is still stored: it is the reviewer's starting point,
        and it is also the only reason a confirmation can ever carry a
        below-floor confidence, which is what
        ``cost_match.confidence_above_floor`` exists to catch. Drop the
        suggestion here and that rule becomes dead code.
        """
        service = CostMatchService(session)
        # A crane-hire line shares one word with the formwork item and stays
        # same-domain (structural): the score lands under the review floor
        # without a domain conflict, which is the case this test exists for.
        # (The old "pump hire for wall panel" pair now reads as a cross-domain
        # pairing - masonry vs structural - and hard-zeroes by design; that is
        # a different contract than the one exercised here.)
        run = await service.create_run(_payload(project_id, [_line("weekly hire of mobile crawler crane for formwork", "m2", "6")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_UNMATCHED
        assert result.suggested_cost_item_id == cost_base["CM-FORMWORK"]
        assert Decimal("0") < result.confidence < Decimal(str(REVIEW_CONFIDENCE))
        assert result.hint_code == "no_good_match"

    async def test_alternatives_are_kept_for_the_override_picker(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("concrete wall formwork", "m2", "120")]))
        (result,) = await _results(service, run)
        assert len(result.alternatives) >= 2
        first = result.alternatives[0]
        assert set(first) >= {"cost_item_id", "code", "unit", "rate", "confidence"}
        # Money and confidence cross the JSON boundary as strings, never floats.
        assert first["rate"] is None or isinstance(first["rate"], str)
        assert isinstance(first["confidence"], str)


# ── batch behaviour ─────────────────────────────────────────────────────────


class TestBatch:
    async def test_identical_lines_are_scored_once(self, cost_base, project_id, session, monkeypatch) -> None:
        """A pasted bill repeats itself; retrieval must not repeat with it."""
        calls: list[str] = []
        original = CostBaseRepository.find_candidates

        async def counted(self, text, **kwargs):
            calls.append(text)
            return await original(self, text, **kwargs)

        monkeypatch.setattr(CostBaseRepository, "find_candidates", counted)

        service = CostMatchService(session)
        line = "Reinforced concrete wall C30/37"
        run = await service.create_run(
            _payload(
                project_id,
                [_line(line, "m3", "10"), _line(line, "m3", "20"), _line("Bewehrungsstahl", "kg", "500")],
            )
        )
        assert len(calls) == 2, f"expected one retrieval per distinct line, got {calls}"

        results = await _results(service, run)
        assert [r.line_no for r in results] == [1, 2, 3]
        assert results[0].suggested_cost_item_id == results[1].suggested_cost_item_id
        assert results[0].source_quantity != results[1].source_quantity

    async def test_counts_are_aggregated_not_stored(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(
            _payload(
                project_id,
                [
                    _line("Reinforced concrete wall C30/37", "m3", "44.3"),
                    _line("Bituminous shingle underlay felt", "m2", "310"),
                ],
            )
        )
        counts = (await service.counts_for_runs([run.id]))[run.id]
        assert counts.total == 2
        assert counts.exact == 1
        assert counts.unmatched == 1
        assert counts.pending == 2
        # The queue is the tiers where the machine is not claiming an answer.
        assert counts.queue_length == 1
        assert run.item_count == 2


# ── decisions ───────────────────────────────────────────────────────────────


class TestDecisions:
    async def test_confirming_snapshots_the_suggestion(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        reviewer = uuid.uuid4()
        decision = await service.record_decision(
            run,
            result,
            MatchDecisionCreate(decision=DECISION_CONFIRMED),
            decided_by=reviewer,
        )
        assert decision.seq == 1
        assert decision.decided_code == "CM-C30-WALL"
        assert decision.decided_rate == Decimal("185.0000")
        assert decision.decided_by == reviewer
        # The confidence the machine was showing at that moment is frozen.
        assert decision.confidence_at_decision == Decimal("1.0000")
        assert decision.tier_at_decision == TIER_EXACT
        assert result.decision_state == DECISION_CONFIRMED

    async def test_overriding_adopts_a_different_item(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        decision = await service.record_decision(
            run,
            result,
            MatchDecisionCreate(decision=DECISION_OVERRIDDEN, cost_item_id=cost_base["CM-FORMWORK"]),
            decided_by=uuid.uuid4(),
        )
        assert decision.decided_code == "CM-FORMWORK"
        assert decision.decided_cost_item_id == cost_base["CM-FORMWORK"]
        assert result.decision_state == DECISION_OVERRIDDEN
        # The suggestion is untouched: the record has to show both what was
        # offered and what was taken.
        assert result.suggested_code == "CM-C30-WALL"

    async def test_the_history_is_append_only(self, cost_base, project_id, session) -> None:
        """Changing your mind adds a row; it does not rewrite the first one."""
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        await service.record_decision(
            run,
            result,
            MatchDecisionCreate(decision=DECISION_OVERRIDDEN, cost_item_id=cost_base["CM-REBAR"]),
            decided_by=uuid.uuid4(),
        )
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_REJECTED), decided_by=uuid.uuid4()
        )
        reloaded = await service.result_repo.get_with_decisions(result.id)
        assert [d.seq for d in reloaded.decisions] == [1, 2, 3]
        assert [d.decision for d in reloaded.decisions] == [
            DECISION_CONFIRMED,
            DECISION_OVERRIDDEN,
            DECISION_REJECTED,
        ]
        assert reloaded.decision_state == DECISION_REJECTED
        assert reloaded.current_decision.seq == 3
        # A rejection adopts nothing at all.
        assert reloaded.current_decision.decided_cost_item_id is None

    async def test_two_rulings_cannot_share_a_sequence(self, cost_base, project_id, session) -> None:
        """The ledger's ordering is enforced by the database, not by the read.

        ``next_seq`` is ``max(seq) + 1``, which two reviewers ruling at the
        same instant can both compute identically - the read takes no lock.
        Without the unique constraint they would both land, the history would
        have two rulings at the same position, and which one is current would
        depend on row order. Here the collision is forced directly, so the
        test fails the day someone drops the constraint from the model.
        """
        from sqlalchemy.exc import IntegrityError

        from app.modules.cost_match.models import MatchDecision

        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        result_id, run_id = result.id, run.id
        await session.commit()

        async with async_session_factory() as second:
            second.add(
                MatchDecision(
                    result_id=result_id,
                    run_id=run_id,
                    seq=1,
                    decision=DECISION_REJECTED,
                    decided_by=uuid.uuid4(),
                )
            )
            with pytest.raises(IntegrityError):
                await second.flush()
            await second.rollback()

        async with async_session_factory() as check:
            surviving = (
                (await check.execute(select(MatchDecision).where(MatchDecision.result_id == result_id))).scalars().all()
            )
            assert [d.decision for d in surviving] == [DECISION_CONFIRMED]

    async def test_confirming_a_line_with_no_suggestion_is_refused(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Bituminous shingle underlay felt", "m2", "310")]))
        (result,) = await _results(service, run)
        with pytest.raises(NoSuggestionToConfirmError):
            await service.record_decision(
                run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
            )

    async def test_an_override_must_name_an_item(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        with pytest.raises(DecisionPayloadError):
            await service.record_decision(
                run, result, MatchDecisionCreate(decision=DECISION_OVERRIDDEN), decided_by=uuid.uuid4()
            )

    async def test_a_rejection_must_not_name_an_item(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        with pytest.raises(DecisionPayloadError):
            await service.record_decision(
                run,
                result,
                MatchDecisionCreate(decision=DECISION_REJECTED, cost_item_id=cost_base["CM-REBAR"]),
                decided_by=uuid.uuid4(),
            )

    async def test_an_override_target_outside_the_run_base_is_refused(self, cost_base, project_id, session) -> None:
        """Two price levels in one bill, with nothing on the row to say so."""
        from app.modules.costs.models import CostItem

        foreign = CostItem(
            code=f"CM-FOREIGN-{uuid.uuid4().hex[:6]}",
            description="Reinforced concrete wall C30/37",
            unit="m3",
            rate="999.00",
            currency="GBP",
            source=TEST_SOURCE,
            region="OE_TEST_OTHER_REGION",
            is_active=True,
        )
        session.add(foreign)
        await session.flush()

        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        with pytest.raises(LookupError):
            await service.record_decision(
                run,
                result,
                MatchDecisionCreate(decision=DECISION_OVERRIDDEN, cost_item_id=foreign.id),
                decided_by=uuid.uuid4(),
            )

    async def test_an_inactive_item_cannot_be_adopted(self, cost_base, project_id, session) -> None:
        from app.modules.costs.models import CostItem

        withdrawn = CostItem(
            code=f"CM-WITHDRAWN-{uuid.uuid4().hex[:6]}",
            description="Withdrawn concrete item",
            unit="m3",
            rate="10.00",
            currency="EUR",
            source=TEST_SOURCE,
            region=TEST_REGION,
            is_active=False,
        )
        session.add(withdrawn)
        await session.flush()

        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        with pytest.raises(LookupError):
            await service.record_decision(
                run,
                result,
                MatchDecisionCreate(decision=DECISION_OVERRIDDEN, cost_item_id=withdrawn.id),
                decided_by=uuid.uuid4(),
            )

    async def test_a_closed_run_refuses_new_rulings(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        run.status = RUN_STATUS_CLOSED
        await session.flush()
        with pytest.raises(RunClosedError):
            await service.record_decision(
                run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
            )


# ── validation through the service ──────────────────────────────────────────


class TestValidationWiring:
    async def test_a_fresh_run_reports_its_open_queue(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        report = await service.validate_run(run)
        fired = {finding.rule_id for finding in report.findings}
        assert "cost_match.review_queue_cleared" in fired

    async def test_a_dimension_trap_survives_confirmation_as_an_error(self, cost_base, project_id, session) -> None:
        """The end-to-end point of the module: a confident-looking wrong answer."""
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m2", "80")]))
        (result,) = await _results(service, run)
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        reloaded = await service.result_repo.get_with_decisions(result.id)
        report = await service.validate_result(reloaded)
        fired = {finding.rule_id for finding in report.findings}
        assert "cost_match.unit_dimension_matches" in fired
        assert report.status == "errors"

    async def test_confirming_a_below_floor_suggestion_is_flagged(self, cost_base, project_id, session) -> None:
        """Reaching ``confidence_above_floor`` through the real matching path.

        The rule can only fire on a confirmed result whose confidence sits
        under the review floor, and that state is only reachable because an
        unmatched line keeps its suggestion. Building the row by hand would
        prove the rule works on a shape the service might never produce, so
        this drives it end to end: score a real line, confirm what it offered,
        and read the finding back.
        """
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("pump hire for wall panel", "m3", "6")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_UNMATCHED
        assert result.confidence < Decimal(str(REVIEW_CONFIDENCE))

        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        reloaded = await service.result_repo.get_with_decisions(result.id)
        report = await service.validate_result(reloaded)
        fired = {finding.rule_id for finding in report.findings}
        assert "cost_match.confidence_above_floor" in fired


# ── relationship loading strategies ─────────────────────────────────────────


class TestRelationshipLoading:
    """The loading policy is only real if something actually walks the links.

    Each test below touches the relationship it is about on an instance that
    would have to emit SQL to satisfy it, which is the only reading that
    distinguishes ``raise_on_sql`` from the default and from ``raise``.
    """

    async def test_run_results_refuses_lazy_sql(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        run_id = run.id
        await session.commit()

        async with async_session_factory() as fresh:
            reloaded = await fresh.get(MatchRun, run_id)
            with pytest.raises(InvalidRequestError):
                _ = reloaded.results

    async def test_run_results_reads_free_when_ordered_eagerly(self, cost_base, project_id, session) -> None:
        """``raise_on_sql`` is the middle ground: an eager load still reads."""
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        run_id = run.id
        await session.commit()

        async with async_session_factory() as fresh:
            stmt = select(MatchRun).where(MatchRun.id == run_id).options(selectinload(MatchRun.results))
            eager = (await fresh.execute(stmt)).scalars().one()
            assert len(eager.results) == 1

    async def test_result_run_backreference_refuses_lazy_sql(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        result_id = result.id
        await session.commit()

        async with async_session_factory() as fresh:
            reloaded = await fresh.get(MatchResult, result_id)
            with pytest.raises(InvalidRequestError):
                _ = reloaded.run

    async def test_result_decisions_load_eagerly(self, cost_base, project_id, session) -> None:
        """The ruling history is the point of a result, so it comes with it."""
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        result_id = result.id
        await session.commit()

        async with async_session_factory() as fresh:
            reloaded = await fresh.get(MatchResult, result_id)
            assert len(reloaded.decisions) == 1
            assert reloaded.current_decision.decision == DECISION_CONFIRMED

    async def test_deleting_a_run_cascades_to_results_and_decisions(self, cost_base, project_id, session) -> None:
        """``raise_on_sql`` on a delete-orphan collection does not block the cascade."""
        from app.modules.cost_match.models import MatchDecision

        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")]))
        (result,) = await _results(service, run)
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        run_id = run.id
        await session.commit()

        async with async_session_factory() as fresh:
            await CostMatchService(fresh).run_repo.delete(run_id)
            await fresh.commit()

        async with async_session_factory() as check:
            remaining_results = (
                (await check.execute(select(MatchResult).where(MatchResult.run_id == run_id))).scalars().all()
            )
            remaining_decisions = (
                (await check.execute(select(MatchDecision).where(MatchDecision.run_id == run_id))).scalars().all()
            )
            assert remaining_results == []
            assert remaining_decisions == []


# ── learned match patterns ──────────────────────────────────────────────────


class TestMatchPatterns:
    """A ruling writes a token-level pattern; a related line reads it back.

    Patterns are global state on purpose - the point of the feature is that
    the next bill is smarter without re-teaching - so the conftest wipes the
    table between tests and these tests carry their own ruling.
    """

    async def test_a_ruling_persists_the_token_bridge(self, cost_base, project_id, session) -> None:
        from app.modules.cost_match.models import MatchPattern

        service = CostMatchService(session)
        run = await service.create_run(
            _payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")])
        )
        (result,) = await _results(service, run)
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )

        pattern = (
            await session.execute(select(MatchPattern).where(MatchPattern.result_id == result.id))
        ).scalars().one()
        assert pattern.verdict == DECISION_CONFIRMED
        assert pattern.cost_item_id == result.suggested_cost_item_id
        assert "concrete" in pattern.query_tokens
        assert pattern.shared_tokens  # the confirmed bridge is the lesson

    async def test_a_rejected_offer_is_demoted_on_the_next_run(
        self, cost_base, project_id, session
    ) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Stahlbetonwand", "m3", "12")]))
        (result,) = await _results(service, run)
        assert result.tier == TIER_HIGH_CONFIDENCE
        await service.record_decision(
            run, result, MatchDecisionCreate(decision=DECISION_REJECTED), decided_by=uuid.uuid4()
        )

        second = await service.create_run(
            _payload(project_id, [_line("Stahlbetonwand", "m3", "12")])
        )
        (second_result,) = await _results(service, second)
        # The refused item is still offered - the person decides, not the
        # pattern - but it can no longer sit in an auto-trusted tier.
        assert second_result.tier == TIER_NEEDS_REVIEW
        assert second_result.suggested_cost_item_id == result.suggested_cost_item_id
        assert "pattern_rejected" in second_result.reason_codes

    async def test_every_scored_candidate_carries_token_evidence(
        self, cost_base, project_id, session
    ) -> None:
        service = CostMatchService(session)
        run = await service.create_run(
            _payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")])
        )
        (result,) = await _results(service, run)
        assert len(result.alternatives) > 1  # all scored rows, not a top-3
        winner = result.alternatives[0]
        assert winner["cost_item_id"] == str(result.suggested_cost_item_id)
        assert "concrete" in winner["matched_tokens"]
        assert "in_pool" in winner

    async def test_a_ruling_propagates_to_identical_pending_lines(
        self, cost_base, project_id, session
    ) -> None:
        """Forty copies of one row are one question, answered once."""
        service = CostMatchService(session)
        line = _line("Reinforced concrete wall C30/37", "m3", "10")
        run = await service.create_run(
            _payload(project_id, [line, _line("Reinforced concrete wall C30/37", "m3", "20"), _line("Gypsum plaster on walls", "m2", "5")])
        )
        results = await _results(service, run)
        target = next(r for r in results if r.line_no == 1)
        await service.record_decision(
            run, target, MatchDecisionCreate(decision=DECISION_CONFIRMED), decided_by=uuid.uuid4()
        )
        states = {r.line_no: r.decision_state for r in await _results(service, run)}
        assert states == {1: DECISION_CONFIRMED, 2: DECISION_CONFIRMED, 3: DECISION_PENDING}
        dup = next(r for r in await _results(service, run) if r.line_no == 2)
        assert dup.current_decision.note == "propagated from line 1"
        assert dup.current_decision.decided_rate == target.suggested_rate


class TestManualRulings:
    """A manual ruling is a human price outside the corpus.

    Same append-only record, same reviewer attribution, same position
    write-back - it just adopts no cost item, which is precisely what makes
    it manual rather than an override.
    """

    async def test_a_manual_ruling_prices_the_line(self, cost_base, project_id, session) -> None:
        service = CostMatchService(session)
        run = await service.create_run(
            _payload(project_id, [_line("Something the base has never seen", "m2", "10")])
        )
        (result,) = await _results(service, run)
        decision = await service.record_decision(
            run,
            result,
            MatchDecisionCreate(decision="manual", rate=Decimal("42.50"), currency="EUR"),
            decided_by=uuid.uuid4(),
        )
        assert result.decision_state == "manual"
        assert decision.decided_cost_item_id is None
        assert decision.decided_rate == Decimal("42.50")
        assert decision.decided_currency == "EUR"

    async def test_a_manual_ruling_needs_a_positive_rate(
        self, cost_base, project_id, session
    ) -> None:
        service = CostMatchService(session)
        run = await service.create_run(_payload(project_id, [_line("Some work", "m2", "5")]))
        (result,) = await _results(service, run)
        for bad in (None, Decimal("0"), Decimal("-3")):
            with pytest.raises(DecisionPayloadError):
                await service.record_decision(
                    run,
                    result,
                    MatchDecisionCreate(decision="manual", rate=bad),
                    decided_by=uuid.uuid4(),
                )

    async def test_a_corpus_ruling_refuses_a_rate(
        self, cost_base, project_id, session
    ) -> None:
        """`rate` on a confirm would read as "confirm at a different price" -
        two answers at once, so it is refused rather than ignored."""
        service = CostMatchService(session)
        run = await service.create_run(
            _payload(project_id, [_line("Reinforced concrete wall C30/37", "m3", "44.3")])
        )
        (result,) = await _results(service, run)
        with pytest.raises(DecisionPayloadError):
            await service.record_decision(
                run,
                result,
                MatchDecisionCreate(decision=DECISION_CONFIRMED, rate=Decimal("10")),
                decided_by=uuid.uuid4(),
            )


class TestWebVerify:
    """The AI estimate extraction: structured records, dedupe, no money moved."""

    def test_a_model_reply_parses_into_estimate_fields(self) -> None:
        from app.modules.cost_match.webverify import parse_estimate_payload

        raw = '{"eligible": true, "family_words": ["кран", "сферичен"], "price": {"min": 30, "max": 55, "currency": "EUR"}, "breakdown": [{"component": "labor", "label": "монтаж", "min": 20, "max": 40}], "variants": [{"modifier": ["неръждаема"], "price_delta": [10, 15]}], "confidence": "high", "reason": "стандартен артикул"}'
        fields = parse_estimate_payload(raw)
        assert fields["eligible"] is True
        assert fields["price_min"] == Decimal("30")
        assert fields["price_max"] == Decimal("55")
        assert fields["price_median"] == Decimal("42.5")  # midpoint computed
        assert fields["family_words"] == ["кран", "сферичен"]
        assert len(fields["breakdown"]) == 1
        assert len(fields["variants"]) == 1

    def test_an_ineligible_line_stays_unpriced(self) -> None:
        from app.modules.cost_match.webverify import parse_estimate_payload

        raw = '{"eligible": false, "reason": "site-specific provisional sum"}'
        fields = parse_estimate_payload(raw)
        assert fields["eligible"] is False
        assert fields["price_median"] is None

    def test_a_prose_reply_is_not_a_price(self) -> None:
        from app.modules.cost_match.webverify import WebVerifyError, parse_estimate_payload

        with pytest.raises(WebVerifyError):
            parse_estimate_payload("The price is probably around 50 EUR.")

    async def test_verify_dedupes_and_never_writes_money(
        self, cost_base, project_id, session, monkeypatch
    ) -> None:
        from app.config import get_settings
        from app.modules.cost_match.models import WebEstimate

        settings = get_settings()
        monkeypatch.setattr(settings, "web_verify_url", "http://verify.test/v1")
        monkeypatch.setattr(settings, "web_verify_model", "test-model")

        async def _fake_fetch(description, unit, **kwargs):
            return {
                "eligible": True,
                "family_words": ["wall"],
                "price_min": Decimal("10"),
                "price_max": Decimal("20"),
                "price_median": Decimal("15"),
                "currency": "EUR",
                "breakdown": [],
                "variants": [],
                "ai_confidence": "medium",
                "reason": "test",
                "raw": "{}",
            }

        monkeypatch.setattr(
            "app.modules.cost_match.service.fetch_estimate", _fake_fetch
        )

        service = CostMatchService(session)
        run = await service.create_run(
            _payload(
                project_id,
                [
                    _line("A line the base cannot price", "m2", "5"),
                    _line("A line the base cannot price", "m2", "9"),
                ],
            )
        )
        totals = await service.run_web_verify(run, scope="all")
        # Two identical lines are one extraction, not two.
        assert totals["unique_lines"] == 1
        assert totals["estimates_written"] == 1
        assert totals["failed"] == 0

        # A second verify reuses the stored record instead of paying again.
        totals2 = await service.run_web_verify(run, scope="all")
        assert totals2["estimates_written"] == 0
        assert totals2["estimates_reused"] == 1

        estimates = (
            await session.execute(select(WebEstimate))
        ).scalars().all()
        assert len(estimates) == 1
        assert estimates[0].price_median == Decimal("15")
        assert estimates[0].status == "suggested"

        # Advisory only: the results are still pending, nothing was ruled.
        results = await _results(service, run)
        assert all(r.decision_state == DECISION_PENDING for r in results)

    async def test_an_unconfigured_endpoint_refuses(self, project_id, session, monkeypatch) -> None:
        from app.config import get_settings
        from app.modules.cost_match.webverify import WebVerifyError

        settings = get_settings()
        monkeypatch.setattr(settings, "web_verify_url", "")
        monkeypatch.setattr(settings, "web_verify_model", "")

        service = CostMatchService(session)
        run = await service.create_run(
            _payload(project_id, [_line("A line", "m2", "1")])
        )
        with pytest.raises(WebVerifyError):
            await service.run_web_verify(run)

    async def test_tool_loop_fetches_sources_then_parses(self) -> None:
        """The endpoint asks for web_search; we run it; the answer parses."""
        import httpx

        from app.modules.cost_match.webverify import fetch_estimate

        ddg_html = (
            '<a class="result__a" href="//duckduckgo.com/l/?uddg='
            "https%3A%2F%2Fsupplier.example%2Fvalve-pn40\">PN40 valve</a>"
            '<a class="result__snippet">Ball valve PN40 1/2" - 8 EUR</a>'
        )
        final_answer = json.dumps(
            {
                "eligible": True,
                "family_words": ["сферичен", "кран"],
                "price": {"min": 6, "max": 10, "median": 8, "currency": "EUR"},
                "breakdown": [],
                "variants": [],
                "confidence": "high",
                "reason": "supplier page says 8 EUR",
            }
        )
        calls: list[dict] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            if "duckduckgo" in str(request.url):
                return httpx.Response(200, text=ddg_html)
            body = json.loads(request.content)
            calls.append(body)
            # First LLM turn: ask for a search. Second turn (after the tool
            # message): emit the answer.
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query": "кран pn40 цена"}',
                                            },
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": final_answer},
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            fields = await fetch_estimate(
                "Сферичен кран PN40 1/2\"",
                "бр.",
                url="http://verify.test/chat",
                api_key="k",
                model="kimi-k3",
                client=client,
                search_provider="duckduckgo",
            )

        assert len(calls) == 2  # model call -> tool result -> model call
        assert calls[0]["tools"]  # tools were offered
        tool_msg = calls[1]["messages"][-1]
        assert tool_msg["role"] == "tool"
        assert "supplier.example/valve-pn40" in tool_msg["content"]
        assert fields["price_median"] == Decimal("8")
        # The fetched search hit lands on the record as provenance.
        assert fields["sources"][0]["url"] == "https://supplier.example/valve-pn40"
