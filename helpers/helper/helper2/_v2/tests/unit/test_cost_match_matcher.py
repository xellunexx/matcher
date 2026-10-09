"""Database-free unit tests for the international cost_match matcher.

Covers accent folding, multilingual synonym matching, metric/imperial unit
normalisation, the explainable confidence score, and the edge-case guards
(empty query, no candidates, ties, unit mismatch, regex-metacharacter input).
Everything here is pure-function and needs no database or network.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.cost_match.matcher import (
    HIGH_CONFIDENCE,
    REVIEW_CONFIDENCE,
    Candidate,
    best_match,
    canonical_tokens,
    explain,
    extract_specs,
    fold_accents,
    no_match_hint,
    normalize_text,
    normalize_unit,
    score_match,
    spec_conflicts,
    suggestion_rate,
    unit_rate_factor,
    units_compatible,
)

# ── Accent folding + normalisation ──────────────────────────────────────────


class TestNormalisation:
    def test_folds_common_diacritics(self) -> None:
        assert fold_accents("béton") == "beton"
        assert fold_accents("Dämmung") == "Dammung"
        assert fold_accents("hormigón") == "hormigon"

    def test_folds_german_sharp_s_and_nordic(self) -> None:
        assert fold_accents("straße") == "strasse"
        assert fold_accents("Ø") == "o"

    def test_leaves_cyrillic_intact(self) -> None:
        assert fold_accents("бетон") == "бетон"

    def test_normalize_text_lowercases_and_collapses(self) -> None:
        assert normalize_text("  Béton   Armé ") == "beton arme"

    def test_normalize_text_handles_none_and_empty(self) -> None:
        assert normalize_text(None) == ""
        assert normalize_text("   ") == ""

    def test_regex_metacharacters_are_safe(self) -> None:
        # Must not raise and must strip metachars to separators.
        assert normalize_text("C30/37 [*+](}") == "c30 37"


# ── Multilingual synonym matching ───────────────────────────────────────────


class TestSynonyms:
    def test_concrete_across_languages_shares_a_concept(self) -> None:
        for word in ("concrete", "Beton", "hormigón", "calcestruzzo", "бетон"):
            assert "concrete" in canonical_tokens(word), word

    def test_reinforced_concrete_wall_matches_across_languages(self) -> None:
        english = "reinforced concrete wall"
        for other in (
            "Stahlbetonwand",  # de (compound: steel-concrete-wall)
            "mur en béton armé",  # fr
            "muro de hormigón armado",  # es
            "железобетонная стена",  # ru is a single compound word, weaker
        ):
            score = score_match(other, english)
            assert score.confidence > 0.0, other

    def test_de_es_fr_reinforced_concrete_are_confident(self) -> None:
        english = "reinforced concrete"
        for other in ("Beton bewehrung", "hormigón armadura", "béton armature"):
            score = score_match(other, english)
            assert score.confidence >= HIGH_CONFIDENCE, (other, score.confidence)

    def test_stopwords_do_not_dilute(self) -> None:
        # "of the" style glue words are dropped, so coverage stays high.
        score = score_match("insulation of the wall", "wall insulation")
        assert score.confidence >= HIGH_CONFIDENCE


# ── Unit normalisation (metric + imperial) ──────────────────────────────────


class TestUnits:
    @pytest.mark.parametrize(
        ("unit", "dimension"),
        [
            ("m2", "area"),
            ("m²", "area"),
            ("SQM", "area"),
            ("sq ft", "area"),
            ("m3", "volume"),
            ("cu yd", "volume"),
            ("m", "length"),
            ("ft", "length"),
            ("kg", "mass"),
            ("lb", "mass"),
            ("pcs", "count"),
            ("Stück", "count"),
        ],
    )
    def test_unit_dimension_metric_and_imperial(self, unit: str, dimension: str) -> None:
        assert normalize_unit(unit) == dimension

    def test_unknown_unit_is_none(self) -> None:
        assert normalize_unit("wibble") is None
        assert normalize_unit(None) is None

    def test_bulgarian_linear_metre_aliases(self) -> None:
        # Bills write the linear metre as мл / м.л. / п.м. / лин.м - all fold
        # onto the same key and must resolve to length, not stay unknown.
        for unit in ("мл", "м.л.", "МЛ", "п.м.", "пм", "лин.м", "пог.м"):
            assert normalize_unit(unit) == "length", unit
            assert units_compatible(unit, "m") is True, unit
            assert unit_rate_factor(unit, "m") == 1, unit

    def test_bulgarian_digit_suffixed_metre_aliases(self) -> None:
        # Bulgarian tenders also write the linear metre as м1 / м.1 - the
        # superscript-1 convention mirroring м²/м³. It is a length, not an
        # unknown unit, and converts 1:1 onto plain м.
        for unit in ("м1", "м.1", "М1", "m1"):
            assert normalize_unit(unit) == "length", unit
            assert units_compatible(unit, "м") is True, unit
            assert unit_rate_factor(unit, "м") == 1, unit
        assert units_compatible("м1", "м2") is False
        assert units_compatible("м1", "м3") is False

    def test_folded_cyrillic_unit_keys_resolve(self) -> None:
        # fold_accents decomposes й -> и, so a table keyed on raw "брой" could
        # never be reached by input "Брой". Keys fold through the same path.
        assert normalize_unit("Брой") == "count"
        assert units_compatible("Брой", "бр") is True
        assert unit_rate_factor("Брой", "бр") == 1

    def test_bulgarian_per_occurrence_units_are_count(self) -> None:
        # Per-възел / per-станция catalogue rates price one occurrence - same
        # dimension as a per-бр bill line, convertible 1:1.
        for unit in ("единица", "Възел", "станция", "шкаф", "к-т", "к-кт", "чифт", "пара", "Комплектен"):
            assert normalize_unit(unit) == "count", unit
            assert units_compatible(unit, "бр") is True, unit

    def test_bulgarian_time_and_lumpsum_units(self) -> None:
        for unit in ("ч", "час", "чч", "машиночас"):
            assert normalize_unit(unit) == "time", unit
        for unit in ("LSUM", "строеж", "обект"):
            assert normalize_unit(unit) == "sum", unit

    def test_qualified_units_resolve_leading_unit(self) -> None:
        # Catalogue units qualified by context lead with the physical unit:
        # "км тръби" is a per-kilometre price, "м сечение" per metre.
        assert normalize_unit("км тръби") == "length"
        assert unit_rate_factor("км тръби", "m") == pytest.approx(Decimal(1) / 1000)
        assert normalize_unit("10 m3 строителен обем") == "volume"
        assert unit_rate_factor("10 m3 строителен обем", "m3") == pytest.approx(Decimal(1) / 10)

    def test_glued_bulk_multiplier(self) -> None:
        # "10m"/"100kg" price ten metres / a hundred kilos per unit.
        assert unit_rate_factor("10m", "m") == pytest.approx(Decimal(1) / 10)
        assert unit_rate_factor("100kg", "kg") == pytest.approx(Decimal(1) / 100)

    def test_metric_imperial_same_dimension_is_compatible(self) -> None:
        assert units_compatible("m2", "sq ft") is True

    def test_area_vs_volume_incompatible(self) -> None:
        assert units_compatible("m2", "m3") is False

    def test_unknown_unit_is_no_signal(self) -> None:
        assert units_compatible("m2", "wibble") is None


# ── Confidence scoring + explainability ─────────────────────────────────────


class TestScoring:
    def test_exact_normalized_match_is_perfect(self) -> None:
        score = score_match("Concrete C30/37", "concrete c30 37")
        assert score.confidence == pytest.approx(1.0)
        assert score.factors["exact"] == 1.0
        assert "exact_match" in score.reasons

    def test_score_is_bounded(self) -> None:
        score = score_match("concrete wall", "reinforced concrete masonry wall plaster")
        assert 0.0 <= score.confidence <= 1.0

    def test_factors_are_exposed_for_audit(self) -> None:
        score = score_match("concrete wall", "concrete slab")
        assert {
            "query_coverage",
            "term_overlap",
            "unit_factor",
            "action_factor",
            "exact",
            "matched_tokens",
            "candidate_coverage",
        } <= set(score.factors)
        assert 0.0 < score.factors["query_coverage"] < 1.0

    def test_unit_mismatch_lowers_confidence(self) -> None:
        with_ok = score_match("concrete", "concrete", query_unit="m3", candidate_unit="cbm")
        with_bad = score_match("concrete", "concrete", query_unit="m3", candidate_unit="m2")
        assert with_bad.confidence < with_ok.confidence
        assert "unit_mismatch" in with_bad.reasons
        assert "unit_match" in with_ok.reasons

    def test_no_token_overlap_scores_zero(self) -> None:
        score = score_match("window", "concrete")
        assert score.confidence == 0.0

    def test_explain_is_localized(self) -> None:
        score = score_match("concrete", "concrete", query_unit="m3", candidate_unit="m2")
        en = explain(score, locale="en")
        de = explain(score, locale="de")
        assert en and de
        assert en != de
        # No raw reason keys leak through.
        assert "match.reason" not in en


# ── best_match orchestration + edge cases ───────────────────────────────────


def _candidates() -> list[Candidate]:
    return [
        Candidate(ref="c1", text="reinforced concrete wall", unit="m3"),
        Candidate(ref="c2", text="brick masonry wall", unit="m2"),
        Candidate(ref="c3", text="interior wall painting", unit="m2"),
    ]


class TestBestMatch:
    def test_picks_best_candidate(self) -> None:
        # German closed compound: steel + concrete + wall glued into one word.
        result = best_match("Stahlbetonwand", _candidates(), query_unit="m3")
        assert result.candidate is not None
        assert result.candidate.ref == "c1"
        assert result.score is not None
        assert result.score.confidence >= REVIEW_CONFIDENCE

    def test_english_query_is_confident(self) -> None:
        result = best_match("reinforced concrete wall", _candidates(), query_unit="m3")
        assert result.candidate is not None
        assert result.candidate.ref == "c1"
        assert result.is_confident is True
        assert result.hint is None

    def test_empty_query_returns_hint_not_crash(self) -> None:
        result = best_match("   ", _candidates())
        assert result.candidate is None
        assert result.score is None
        assert result.hint == no_match_hint("empty_query")

    def test_no_candidates_returns_hint(self) -> None:
        result = best_match("concrete wall", [])
        assert result.candidate is None
        assert result.hint == no_match_hint("no_candidates")

    def test_low_confidence_returns_hint_but_still_offers_context(self) -> None:
        result = best_match("photovoltaic inverter firmware", _candidates())
        assert result.is_confident is False
        assert result.hint == no_match_hint("no_good_match")
        assert result.score is not None
        assert result.score.confidence < REVIEW_CONFIDENCE

    def test_ties_are_flagged_and_resolved_by_order(self) -> None:
        cands = [
            Candidate(ref="a", text="concrete wall"),
            Candidate(ref="b", text="concrete wall"),
        ]
        result = best_match("concrete wall", cands)
        assert result.tie is True
        assert result.candidate is not None
        assert result.candidate.ref == "a"  # input order breaks the tie

    def test_regex_metacharacter_query_is_handled(self) -> None:
        cands = [Candidate(ref="c1", text="concrete C30/37", unit="m3")]
        result = best_match("concrete C30/37 [*+]", cands, query_unit="m3")
        assert result.candidate is not None
        assert result.candidate.ref == "c1"

    def test_alternatives_are_ranked(self) -> None:
        result = best_match("wall", _candidates(), top_n=3)
        confidences = [score.confidence for _, score in result.alternatives]
        assert confidences == sorted(confidences, reverse=True)

    def test_exact_twin_outranks_a_confidence_saturated_partial(self) -> None:
        """A verbatim corpus row must beat a partial that also reached 1.0.

        Two shared content tokens already saturate the confidence cap, so a
        generic "demolish old X" row ties at 1.0 with the line's own verbatim
        twin. The tiebreak compares candidate_coverage and matched_tokens -
        factors the exact-match path must populate, or the twin sinks below
        every weaker row in the pool.
        """
        cands = [
            Candidate(ref="partial", text="ДЕМОНТАЖ НА СТАРА ХИДРОИЗОЛАЦИЯ", unit="м2"),
            Candidate(ref="exact", text="ДЕМОНТАЖ НА СТАРА ЧЕРНА ХАРТИЯ", unit="м2"),
        ]
        result = best_match("ДЕМОНТАЖ НА СТАРА ЧЕРНА ХАРТИЯ", cands, query_unit="м2")
        assert result.candidate is not None
        assert result.candidate.ref == "exact"
        assert result.score is not None
        assert "exact_match" in result.score.reasons
        assert result.score.factors["candidate_coverage"] == 1.0
        assert result.score.factors["matched_tokens"] == 4.0

    def test_exact_twin_in_a_foreign_unit_still_wins_but_is_flagged(self) -> None:
        """A verbatim twin is always the pick - the unit penalty stays honest.

        The twin is real evidence and must surface as the suggestion, but
        its confidence keeps the unit-mismatch penalty so it lands in review
        rather than silently applying a per-m3 rate to a per-m2 line.
        """
        cands = [
            Candidate(ref="partial_right_unit", text="ДЕМОНТАЖ НА СТАРА ХИДРОИЗОЛАЦИЯ", unit="м2"),
            Candidate(ref="exact_wrong_unit", text="ДЕМОНТАЖ НА СТАРА ЧЕРНА ХАРТИЯ", unit="м3"),
        ]
        result = best_match(
            "ДЕМОНТАЖ НА СТАРА ЧЕРНА ХАРТИЯ", cands, query_unit="м2"
        )
        assert result.candidate is not None
        assert result.candidate.ref == "exact_wrong_unit"
        assert result.score is not None
        assert "unit_mismatch" in result.score.reasons
        assert result.is_confident is False

    def test_exact_twin_is_not_overridden_by_the_median_pool(self) -> None:
        """A verbatim row must keep its own rate on composite-scope lines.

        "Доставка и монтаж" opens the pooled-median path, where every
        same-scope same-unit candidate is treated as one product's price
        observation. When the winner is a word-for-word twin the pool is
        not evidence - the twin's own rate is.
        """
        cands = [
            Candidate(
                ref="twin",
                text="ДОСТАВКА И МОНТАЖ НА ВОДОСТОЧНИ КАЗАНЧЕТА",
                unit="бр",
                payload={"unit_rate": Decimal("45")},
            ),
            Candidate(
                ref="pool_a",
                text="ДОСТАВКА И МОНТАЖ НА ВОДОСТОЧНИ ТРЪБИ",
                unit="бр",
                payload={"unit_rate": Decimal("180")},
            ),
            Candidate(
                ref="pool_b",
                text="ДОСТАВКА И МОНТАЖ НА ВОДОСТОЧНИ КОЛЯНА",
                unit="бр",
                payload={"unit_rate": Decimal("8")},
            ),
        ]
        result = best_match(
            "ДОСТАВКА И МОНТАЖ НА ВОДОСТОЧНИ КАЗАНЧЕТА", cands, query_unit="бр"
        )
        assert result.candidate is not None
        assert result.candidate.ref == "twin"
        # The pool must not run: no foreign median may pin itself onto the twin.
        assert result.median_rate is None
        assert result.median_candidate is None
        assert result.pool_size == 0


# ── Declared-spec conflicts ─────────────────────────────────────────────────


class TestSpecConflicts:
    def test_typed_specs_extract(self) -> None:
        specs = extract_specs("Тръба PE100 Ф75 PN10")
        assert specs["diameter"] == {"75"}
        assert specs["pressure"] == {"10"}

    def test_cm_and_decimal_metre_are_one_value(self) -> None:
        # 10 cm and 0,10 m are the same dimension; a comma decimal must not
        # read as two separate numbers.
        assert extract_specs("Настилка 10 cm") == extract_specs("Замазка 0,10 м")
        assert spec_conflicts("Настилка 10 cm", "Замазка 0,10 м") == []

    def test_disjoint_declared_values_conflict(self) -> None:
        assert spec_conflicts("Тръба Ф75 PN10", "Тръба Ф110 PN10") == ["diameter"]
        assert spec_conflicts("Настилка 10 cm", "Замазка 15 cm") == ["dim_len"]
        assert spec_conflicts("Бетон C30/37", "Beton C25/30") == ["concrete"]

    def test_silence_is_never_a_conflict(self) -> None:
        # A row that declares no diameter may still be the right generic
        # price - only two declared, disjoint sets disagree.
        assert spec_conflicts("Тръба Ф75 PN10", "Тръба PN10 за вода") == []
        assert spec_conflicts("Тръба за вода", "Тръба Ф110 PN10") == []

    def test_spec_conflict_halves_confidence(self) -> None:
        good = score_match("тръба полиетиленова ф75 pn10", "тръба полиетиленова ф75 pn10")
        bad = score_match("тръба полиетиленова ф75 pn10", "тръба полиетиленова ф110 pn10")
        assert bad.confidence < good.confidence
        assert "spec_conflict" in bad.reasons
        assert bad.factors["spec_conflict"] == 1.0
        # A declared Ф75 vs Ф110 disagreement must drop a verbatim-looking
        # row out of the confident band even when the prose is identical.
        assert bad.confidence < HIGH_CONFIDENCE

    def test_bracket_ceiling_differs_from_declared_thickness(self) -> None:
        # "up to 0,5 m" is a price bracket, not the slab's thickness - it
        # conflicts with a declared 12 cm and so lands in review, where a
        # person supplies the real figure.
        assert spec_conflicts("плочници 12 cm", "събаряне при дебелина до 0,5 м") == ["dim_len"]

    def test_razbivane_maps_to_demolition_concept(self) -> None:
        assert "demolition" in canonical_tokens("разбиване")
        assert "demolition" in canonical_tokens("разбивка")


# ── Decimal-exact money pass-through ────────────────────────────────────────


class TestMoneyPassThrough:
    def test_decimal_rate_is_preserved_exactly(self) -> None:
        cand = Candidate(ref="c1", text="concrete", payload={"unit_rate": Decimal("123.45")})
        assert suggestion_rate(cand) == Decimal("123.45")

    def test_string_rate_parsed_without_float_error(self) -> None:
        cand = Candidate(ref="c1", text="concrete", payload={"unit_rate": "0.10"})
        rate = suggestion_rate(cand)
        assert rate == Decimal("0.10")
        # Decimal preserves the exact value that a float would corrupt.
        assert rate + Decimal("0.20") == Decimal("0.30")

    def test_missing_or_unparseable_rate_is_none(self) -> None:
        assert suggestion_rate(Candidate(ref="c1", text="concrete")) is None
        assert suggestion_rate(Candidate(ref="c1", text="x", payload={"unit_rate": "n/a"})) is None


class TestActionFamily:
    """Work-type verb polarity: demolition vs installation must not match."""

    def test_demolition_line_against_install_row_conflicts(self) -> None:
        score = score_match(
            "Демонтаж на теракот под- санитарни помещения",
            "Доставка и направа на облицовка по стени от фаянс в кухня",
            query_unit="м2", candidate_unit="м2",
        )
        assert "action_conflict" in score.reasons
        assert score.factors["action_factor"] == pytest.approx(0.5)
        assert score.confidence < REVIEW_CONFIDENCE

    def test_install_line_against_demolition_row_conflicts(self) -> None:
        score = score_match(
            "Доставка и монтаж на керемиди",
            "ДЕМОНТАЖ НА КЕРЕМИДИ ВКЛ. СВАЛЯНЕ",
            query_unit="м2", candidate_unit="м2",
        )
        assert "action_conflict" in score.reasons
        assert score.confidence < REVIEW_CONFIDENCE

    def test_same_family_marks_action_match(self) -> None:
        score = score_match(
            "Разваляне на тухлена зидария",
            "РАЗВАЛЯНЕ НА ТУХЛЕНА ЗИДАРИЯ 250 ММ",
            query_unit="м3", candidate_unit="м3",
        )
        assert "action_match" in score.reasons
        assert "action_conflict" not in score.reasons

    def test_undeclared_candidate_family_stays_neutral(self) -> None:
        # A catalogue row with no work verb declares no family, so nothing
        # is certain enough to penalise - it stays reviewable on its nouns.
        score = score_match(
            "Къртене на мазилка",
            "ВЪТРЕШНА ВАРОВА МАЗИЛКА ПО СТЕНИ",
            query_unit="м2", candidate_unit="м2",
        )
        assert "action_conflict" not in score.reasons
        assert score.factors["action_factor"] == 1.0

    def test_delivery_is_not_an_action_family(self) -> None:
        # Nearly every Bulgarian line says "доставка"; it must never seed
        # a family on its own, or everything would conflict with everything.
        score = score_match(
            "Доставка на керемиди",
            "ПОЛАГАНЕ НА КЕРЕМИДИ",
            query_unit="м2", candidate_unit="м2",
        )
        assert "action_conflict" not in score.reasons

    def test_izzizhdane_is_construction_not_demolition(self) -> None:
        # "Иззиждане" = erecting masonry (bricking an opening shut) - the
        # process, not the teardown. It must share the installation concept
        # and never collide with the demolition family.
        assert "installation" in canonical_tokens("Иззиждане")
        assert "demolition" not in canonical_tokens("Иззиждане")

    def test_kurtene_maps_to_demolition_concept(self) -> None:
        assert "demolition" in canonical_tokens("Къртене")
        assert "demolition" in canonical_tokens("разкъртване")


class TestBoilerplateTailCut:
    """"вкл…" / ". крайна цена" / "съгласно …" tails declare price scope, not
    product identity: the match head ends at the first such marker."""

    def test_vkl_tail_twin_is_exact(self) -> None:
        score = score_match(
            "Доставка и полагане на геотекстил, включително всички свързани "
            "с това разходи, съгласно изискванията на ТС",
            "Доставка и полагане на геотекстил. крайна цена",
            query_unit="м2", candidate_unit="м2",
        )
        assert score.factors["exact"] == 1.0
        assert score.confidence == pytest.approx(1.0)
        assert "exact_match" in score.reasons

    def test_vkl_abbreviation_keeps_included_work(self) -> None:
        score = score_match(
            "ДЕМОНТАЖ НА КЕРЕМИДИ ВКЛ. СВАЛЯНЕ",
            "Демонтаж на керемиди",
            query_unit="м2", candidate_unit="м2",
        )
        assert score.factors["exact"] == 0.0
        assert score.confidence < HIGH_CONFIDENCE

    def test_head_twin_beats_pooled_partial(self) -> None:
        # The lamination row shares the "включително всички…" boilerplate but
        # is a different product: head truncation must keep it out of the
        # exact race AND out of the median pool.
        result = best_match(
            "Доставка и полагане на геотекстил, включително всички свързани "
            "с това разходи, съгласно изискванията на ТС",
            [
                Candidate(
                    ref="lam",
                    text="Доставка и монтаж на поли и обшивки от поцинкована "
                    "ламарина около комини, капандури, табакери, включително "
                    "крепежи всички необходими дейности за монтажа",
                    unit="м2",
                    payload={"unit_rate": "8.95", "currency": "EUR", "code": "OPR2-000052"},
                ),
                Candidate(
                    ref="twin",
                    text="Доставка и полагане на геотекстил. крайна цена",
                    unit="м2",
                    payload={"unit_rate": "4.99", "currency": "EUR", "code": "OPR3-001043"},
                ),
            ],
            query_unit="м2",
        )
        assert result.candidate is not None and result.candidate.ref == "twin"
        assert result.score is not None and result.score.factors["exact"] == 1.0
        assert result.median_rate is None

    def test_krayna_tsena_alone_cuts(self) -> None:
        score = score_match(
            "Монтаж на спирателен кран ф32",
            "Монтаж на спирателен кран ф32. крайна цена",
            query_unit="бр", candidate_unit="бр",
        )
        assert score.factors["exact"] == 1.0

    def test_head_difference_still_matters(self) -> None:
        # Same boilerplate, different heads: must NOT be a twin.
        score = score_match(
            "Доставка и полагане на геотекстил, вкл. разходи",
            "Доставка и полагане на геомрежа. крайна цена",
            query_unit="м2", candidate_unit="м2",
        )
        assert score.factors["exact"] == 0.0

    def test_pool_excludes_foreign_currency(self) -> None:
        # A median across mixed denominations is not a price: the pool only
        # admits rows sharing the winning candidate's currency.
        result = best_match(
            "Доставка и монтаж на тръби ф50",
            [
                Candidate(
                    ref="eur-a",
                    text="Доставка и монтаж на тръби ф50 едно",
                    unit="м",
                    payload={"unit_rate": "10", "currency": "EUR", "code": "A"},
                ),
                Candidate(
                    ref="usd-b",
                    text="Доставка и монтаж на тръби ф50 две",
                    unit="м",
                    payload={"unit_rate": "999", "currency": "USD", "code": "B"},
                ),
            ],
            query_unit="м",
        )
        if result.median_rate is not None:
            assert result.pool_refs <= {r for r in result.pool_refs}
            # USD member must not enter the EUR pool
            usd_in_pool = any(
                ref == "usd-b" for ref in result.pool_refs
            )
            assert not usd_in_pool


class TestMasonrySafeUnits:
    """Brickwork is bought per м². A м³ brick line with no declared
    thickness still answers to the per-м² price book, and a per-м³ brick
    candidate is only eligible when a thickness makes the volume real."""

    def test_area_candidate_cannot_price_a_volume_brick_line(self) -> None:
        score = score_match(
            "Иззиждане на отвори с тухла",
            "Зидане на преградни стени от тухла",
            query_unit="м³", candidate_unit="м²",
        )
        assert score.factors["unit_factor"] < 1.0
        assert "unit_mismatch" in score.reasons

    def test_volume_candidate_uses_the_same_declared_dimension(self) -> None:
        score = score_match(
            "Иззиждане на отвори с тухла",
            "Зидане на преградни стени от тухла",
            query_unit="м³", candidate_unit="м³",
        )
        assert score.factors["unit_factor"] == 1.0
        assert "unit_match" in score.reasons

    def test_volume_candidate_with_thickness_stays_eligible(self) -> None:
        score = score_match(
            "Иззиждане на отвори с тухла",
            "Доставка и изграждане на тухлена зидария с дебелина 25см, "
            "с керамични решетъчни тухли, на вароциментов разтвор",
            query_unit="м³", candidate_unit="м³",
        )
        assert score.factors["unit_factor"] == 1.0
        assert "masonry_m3_thickness" in score.reasons

    def test_query_thickness_keeps_volume_candidates_eligible(self) -> None:
        score = score_match(
            "Иззиждане на 25 см. тухлен зид",
            "Зидане на тухлен зид",
            query_unit="м³", candidate_unit="м³",
        )
        assert score.factors["unit_factor"] == 1.0
        assert "masonry_m3_thickness" in score.reasons

    def test_izzizhdane_alone_triggers_the_rule(self) -> None:
        # "Иззиждане" means bricklaying even without the word "тухла".
        score = score_match(
            "Иззиждане на отвори",
            "Зидане на отвори в прегради",
            query_unit="м³", candidate_unit="м²",
        )
        assert score.factors["unit_factor"] < 1.0
        assert "unit_mismatch" in score.reasons

    def test_same_dimension_row_wins_over_area_row(self) -> None:
        result = best_match(
            "Иззиждане на отвори с тухла",
            [
                Candidate(ref="m3", text="Зидане на отвори с тухла", unit="м³",
                          payload={"unit_rate": "145", "currency": "EUR"}),
                Candidate(ref="m2", text="Зидане на отвори с тухла", unit="м²",
                          payload={"unit_rate": "25", "currency": "EUR"}),
            ],
            query_unit="м³",
        )
        assert result.candidate is not None and result.candidate.ref == "m3"

    def test_non_masonry_volume_pairs_are_untouched(self) -> None:
        # Reinforced-concrete walls legitimately price per м³: the brickwork
        # rule must not leak into generic "стена" text.
        score = score_match(
            "Стоманобетонова стена дебелина 20см",
            "Стоманобетонова стена",
            query_unit="м³", candidate_unit="м²",
        )
        assert score.factors["unit_factor"] < 1.0
        assert "masonry_m2_equiv" not in score.reasons
        assert "masonry_m3_no_thickness" not in score.reasons

    def test_concrete_volume_to_volume_untouched(self) -> None:
        score = score_match(
            "Доставка на бетон C30/37",
            "Бетон C30/37",
            query_unit="м³", candidate_unit="м³",
        )
        assert score.factors["unit_factor"] == 1.0
        assert "unit_match" in score.reasons

    def test_verbatim_m3_twin_is_not_penalized(self) -> None:
        # The learned ruling "Иззиждане на отвори с тухла" @ м³ is the SAME
        # row word-for-word: the no-thickness rule gates foreign evidence,
        # never the operator's own declared line.
        score = score_match(
            "Иззиждане на отвори с тухла",
            "Иззиждане на отвори с тухла",
            query_unit="м³", candidate_unit="м³",
        )
        assert score.factors["exact"] == 1.0
        assert score.factors["unit_factor"] == 1.0
        assert score.confidence == pytest.approx(1.0)
        assert "masonry_m3_verbatim" in score.reasons
