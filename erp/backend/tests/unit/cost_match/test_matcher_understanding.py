# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Pure (no-DB) tests for the matcher understanding layer.

Every behaviour here was a measured golden-harness failure first:

* a personnel-training line nearly bridged to an HVAC chiller on the mass
  noun "работа" (0.29, one stem below the review floor);
* the bill's Latin ``m`` and the corpus's Cyrillic ``м`` counted as
  different units, letting a per-КИЛОМЕТЪР rate win a per-метър line;
* "долен слой" (lower layer) priced as "горен слой" (upper layer);
* a divergent two-price pool reported a median no supplier ever quoted.

These tests stay away from the database entirely - the matcher's contract
is pure functions, and the understanding layer keeps that contract.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.cost_match.domains import domain_of, domains_conflict
from app.modules.cost_match.matcher import (
    REVIEW_CONFIDENCE,
    Candidate,
    best_match,
    score_match,
    spec_conflicts,
    unit_rate_factor,
    units_compatible,
)


class TestDomainGate:
    def test_training_line_cannot_match_chiller(self) -> None:
        score = score_match(
            "Обучение на персонал за работа със системата",
            "Въздухо-охлаждаем водо-охлаждащ агрегат за външен монтаж Инверторен чилър",
            query_unit="бр.",
            candidate_unit="бр.",
        )
        assert score.confidence == 0.0
        assert any(r.startswith("domain_conflict:services:hvac") for r in score.reasons)

    def test_unknown_side_never_blocks(self) -> None:
        assert domain_of("qxz something unclassifiable") is None
        assert domains_conflict(None, "hvac") is False
        assert domains_conflict("services", None) is False

    def test_adjacent_trades_are_not_conflicts(self) -> None:
        assert domains_conflict("plumbing", "hvac") is False
        assert domains_conflict("roofing", "waterproofing") is False

    def test_disjoint_trades_conflict(self) -> None:
        assert domains_conflict("services", "hvac") is True
        assert domains_conflict("plumbing", "elv") is True

    def test_classifier_cases(self) -> None:
        assert domain_of("Обучение на персонал за работа със системата") == "services"
        assert domain_of("PVC-U коляно - 90° - лепене Ø 50") == "plumbing"
        assert domain_of("Масов машинен изкоп") == "earthworks"
        assert domain_of("Доставка и монтаж на кабел СВТ 3х2,5") == "electrical"


class TestCrossScriptUnits:
    def test_latin_m_is_cyrillic_m(self) -> None:
        assert units_compatible("m", "м") is True
        assert units_compatible("m2", "м²") is True

    def test_metre_and_kilometre_convert_across_scripts(self) -> None:
        assert float(unit_rate_factor("км", "m")) == pytest.approx(0.001)
        assert float(unit_rate_factor("м", "km")) == pytest.approx(1000)

    def test_incompatible_dimensions_still_refuse(self) -> None:
        assert unit_rate_factor("м2", "м3") is None

    def test_same_unit_twin_beats_kilometre_twin(self) -> None:
        result = best_match(
            "Трасиране на кабелна линия",
            [
                Candidate(ref="KM", text="Трасиране на кабелна линия", unit="км",
                          payload={"unit_rate": "1250", "currency": "EUR"}),
                Candidate(ref="M", text="Трасиране на кабелна линия", unit="м",
                          payload={"unit_rate": "1.0", "currency": "EUR"}),
            ],
            query_unit="m",
        )
        assert result.candidate is not None
        assert result.candidate.ref == "M"


class TestAttributeConflicts:
    def test_lower_layer_is_not_upper_layer(self) -> None:
        assert "position_vertical" in spec_conflicts(
            "Доставка и полагане на долен слой от каучук",
            "Доставка и полагане на горен слой EPDM гранули",
        )

    def test_inner_thread_is_not_outer_thread(self) -> None:
        assert "thread_side" in spec_conflicts(
            "PVC-U холендер - лепене/вътр. резба Ø 50",
            "PVC-U холендер - лепене/външ. резба Ø 50",
        )

    def test_size_class_conflict(self) -> None:
        assert "size_class" in spec_conflicts(
            "Засаждане на едроразмерна широколистна растителност",
            "Засаждане на средноразмерна широколистна растителност",
        )

    def test_no_conflict_when_silent(self) -> None:
        assert spec_conflicts(
            "Доставка и монтаж на врати",
            "Доставка и монтаж на самозатварящи се врати",
        ) == []


class TestPoolHonesty:
    def test_diverged_pool_produces_no_blended_median(self) -> None:
        result = best_match(
            "Доставка и монтаж на дъсчена обшивка",
            [
                Candidate(ref="A", text="Доставка и монтаж на дъсчена обшивка с дебелина 2.5 см", unit="м2",
                          payload={"unit_rate": "24.8", "currency": "EUR"}),
                Candidate(ref="B", text="Доставка и монтаж на дъсчена обшивка. Дебелина 2х10 см", unit="м2",
                          payload={"unit_rate": "130.0", "currency": "EUR"}),
            ],
            query_unit="м2",
        )
        assert result.pool_size == 0  # missing thickness is not a price pool
        assert result.median_rate is None  # no blended 77.4 fiction
        assert result.candidate is not None

    def test_one_shared_noun_does_not_build_a_pool(self) -> None:
        result = best_match(
            "Доставка и монтаж на врати - летящи за рехабилитационна площадка",
            [
                Candidate(ref="SELF", text="Доставка и монтаж на самозатварящи се врати", unit="бр.",
                          payload={"unit_rate": "551", "currency": "EUR"}),
                Candidate(ref="TWIN", text="Доставка и монтаж на врати - летящи за рехабилитационна и фитнес площадка", unit="бр.",
                          payload={"unit_rate": "250", "currency": "EUR"}),
            ],
            query_unit="бр.",
        )
        # The 551 self-closing door shares only "врата" with the line and
        # must stay out of the price pool; the true twin defines it alone.
        assert "SELF" not in result.pool_refs
        if result.median_rate is not None:
            assert result.median_rate == Decimal("250")


class TestGenericStemFloor:
    def test_mass_noun_stems_carry_no_content(self) -> None:
        score = score_match(
            "Обучение на персонал",
            "Ремонт на система",
            query_unit="бр.",
            candidate_unit="бр.",
        )
        assert score.confidence < REVIEW_CONFIDENCE
