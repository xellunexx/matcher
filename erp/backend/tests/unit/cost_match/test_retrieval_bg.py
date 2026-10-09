"""Conjunctive retrieval and its non-negotiable price-safety boundary."""

from app.modules.cost_match.bulgarian import parse_work
from app.modules.cost_match.matcher import Candidate, best_match
from app.modules.cost_match.retrieval_bg import retrieval_plan
from app.modules.cost_match.work_catalog import work_metadata

DEMOLITION = 'Разваляне на 25 см. тухлен зид'


def test_bulgarian_wall_synonyms_retrieve_all_applicable_quotes() -> None:
    plan = retrieval_plan(DEMOLITION)
    assert [(group.kind, group.value) for group in plan.groups] == [
        ('object', 'masonry'), ('operation', 'demolish'), ('material', 'brick'),
    ]
    for quote in (
        'Разваляне на 25 см тухлен зид',
        'Събаряне на 25 см тухлена стена',
        'Разбиване на 25 см тухлена зидария',
        'Премахване на 25 см тухлен зид',
        'Отстраняване на 25 см тухлена стена',
    ):
        assert plan.matches(quote), quote
        assert parse_work(quote).object == 'masonry'


def test_building_a_brick_wall_is_not_demolition() -> None:
    building = retrieval_plan('Изграждане на тухлен зид')
    assert building.matches('Направа на тухлена зидария')
    assert building.matches('Изграждане на тухлена стена')
    assert not building.matches('Събаряне на тухлена стена')
    assert not retrieval_plan(DEMOLITION).matches('Направа на тухлена зидария')


def test_secondary_wall_cannot_stand_in_for_primary_object() -> None:
    plan = retrieval_plan(DEMOLITION)
    for quote in ('Премахване на мазилка от тухлен зид',
                  'Разваляне на кофраж пред тухлен зид',
                  'Разбиване на замазка до тухлена стена'):
        assert not plan.matches(quote), quote
    assert not plan.matches('Разваляне на кофраж')
    assert not plan.matches('Разваляне на зид от газобетон')


def test_dimension_unit_and_price_evidence_remain_final_gates() -> None:
    plan = retrieval_plan(DEMOLITION)
    quote = 'Разваляне на 12 см тухлен зид'
    assert plan.matches(quote)
    result = best_match(DEMOLITION, [Candidate('wrong', quote, 'м²',
                                               {'unit_rate': '30', 'currency': 'EUR'})],
                        query_unit='м²', locale='bg')
    assert not result.is_confident
    result = best_match(DEMOLITION, [Candidate('wrong', DEMOLITION, 'м³',
                                               {'unit_rate': '30', 'currency': 'EUR'})],
                        query_unit='м²', locale='bg')
    assert not result.is_confident


def test_unknown_object_needs_an_original_verbatim_quote_not_pieces() -> None:
    line = 'Доставка и монтаж на чела стълби 5 бр.x1.75м. Гранит'
    plan = retrieval_plan(line)
    assert plan.exact_text == line.casefold()
    assert plan.matches(line)
    assert not plan.matches('Доставка и монтаж на чела стълби 10 бр.x1.75м. Гранит')
    assert not retrieval_plan('Ф25').matches('Ф25')


def test_unstated_material_is_not_a_requirement() -> None:
    plan = retrieval_plan('Разваляне на зид')
    assert plan.requirements() == {'object': 'masonry', 'operations': ['demolish']}
    assert plan.matches('Разваляне на тухлен зид')
    assert plan.matches('Разваляне на зид от газобетон')


def test_equivalent_role_aliases_cannot_hide_a_price_conflict() -> None:
    alias = 'Отстраняване на 25 см тухлена стена'
    assert work_metadata(alias)['fingerprint'] == work_metadata(DEMOLITION)['fingerprint']
    assert work_metadata('Разбиване на 25 см тухлена зидария')['fingerprint'] == work_metadata(DEMOLITION)['fingerprint']
    extra = 'Отстраняване на 25 см тухлена стена със специална машина'
    assert work_metadata(extra)['fingerprint'] != work_metadata(DEMOLITION)['fingerprint']
    result = best_match(DEMOLITION, [
        Candidate('original', DEMOLITION, 'м²', {'unit_rate': '30', 'currency': 'EUR'}),
        Candidate('alias', alias, 'кв.м.', {'unit_rate': '95', 'currency': 'EUR'}),
    ], query_unit='м²', locale='bg')
    assert not result.is_confident
    assert result.score.factors['canonical_price_disagreement'] == 1.0
