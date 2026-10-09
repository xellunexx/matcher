from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.cost_match.bulgarian import compare_work
from app.modules.cost_match.matcher import Candidate, best_match, score_match, suggestion_rate
from app.modules.cost_match.retrieval_bg import retrieval_plan
from app.modules.cost_match.work_catalog import canonical_work, work_metadata
from app.modules.costs.models import CostItem

TILE_QUOTE = 'Демонтаж на облицовки и настилки от плочки (теракота, фаянс и гранитогрес). крайна цена'
SCREED_QUOTE = ('Направа /доставка и полагане/ на саморазливна замазка по подове '
                'с дебелина на слоя от 2 до 10мм "Ceresit CN69". крайна цена')
REVEAL_QUOTE = ('Обръщане /доставка и монтаж/ на страници на врати и прозорци '
                'с обикновен гипсокартон до 30см. крайна цена')
WALL_PLASTER = ('Направа /доставка и полагане/ на гладка вароциментова основна мазилка '
                'по стени при ниско влагонатоварване, напр. "Рьофикс 510" или еквивалентна. крайна цена')
CEILING_PLASTER = ('Направа /доставка и полагане/ на гипсо-варова мазилка по вътрешни '
                   'стени и тавани за сухи помещения, напр."Рьофикс 150" или еквивалентна. крайна цена')


def quote(text, rate=7.96, unit='м²', source='operator_pricelist', code='fixture'):
    return Candidate('evidence', text, unit, {
        'unit_rate': str(rate), 'currency': 'EUR', 'source': source,
        'code': code,
        'auto_pricing_eligible': source == 'operator_pricelist',
        'provenance_status': 'approved' if source == 'operator_pricelist' else 'pending_review',
    })


@pytest.mark.parametrize('substrate', ['от тухлена стена', 'по стени от тухли'])
def test_removal_target_and_substrate_have_separate_roles(substrate):
    text = 'Очукване на варова мазилка ' + substrate
    work = canonical_work(text)
    assert work.object == 'plaster'
    assert work.operations == ('demolish',)
    assert work.materials == ('lime',)
    assert work.attributes['substrate_material'] == ('brick',)
    assert 'brick' not in retrieval_plan(text).requirements()['materials']
    assert not retrieval_plan(text).matches('Разваляне на тухлен зид')
    assert canonical_work('Разваляне на тухлен зид').materials == ('brick',)
    metadata = work_metadata(text)
    assert metadata['action_vector'] == ('demolish',)
    assert metadata['target_vector']['object'] == 'plaster'
    assert metadata['target_vector']['attributes']['substrate_material'] == ('brick',)
    assert best_match(text, [quote('Очукване на варова мазилка по стени')], query_unit='м²').is_confident
    assert not best_match(text, [quote('Разваляне на тухлен зид')], query_unit='м²').is_confident
    assert not best_match(text, [quote('Очукване на варова мазилка от бетонна стена')],
                          query_unit='м²').is_confident


@pytest.mark.parametrize('height', ['2.25', '3,50'])
def test_tile_removal_job_is_independent_of_project_wall_height(height):
    query = f'Демонтаж на фаянс по стени в санитарни помещения h={height} м'
    work = canonical_work(query)
    assert work.contexts['project:wall_height_m'] == (format(Decimal(height.replace(',', '.')).normalize(), 'f'),)
    assert not work.specs
    assert retrieval_plan(query).matches(TILE_QUOTE)
    assert compare_work(query, TILE_QUOTE) == ([], [])
    result = best_match(query, [quote(TILE_QUOTE)], query_unit='м²')
    assert result.is_confident
    assert suggestion_rate(result.candidate) == Decimal('7.96')


@pytest.mark.parametrize('text', [
    'Доставка и полагане на фаянс по стени',
    'Демонтаж на мраморни плочи по стени',
    TILE_QUOTE + ' без демонтаж',
])
def test_tile_removal_pattern_keeps_operation_material_and_veto(text):
    query = 'Демонтаж на фаянс по стени h=2.25 м'
    result = best_match(query, [quote(text)], query_unit='м²')
    assert not result.is_confident


def test_self_levelling_job_uses_approved_project_thickness_policy():
    query = 'Полагане на саморазливна замазка 2 см по под'
    work = canonical_work(query)
    assert not work.specs
    assert work.contexts['project:dim_len'] == ('0.02',)
    assert canonical_work(SCREED_QUOTE).contexts['project:layer_thickness_range'] == ('от 2 до 10мм',)
    result = best_match(query, [quote(SCREED_QUOTE, 4.6)], query_unit='м²')
    assert result.is_confident
    assert suggestion_rate(result.candidate) == Decimal('4.6')


def test_proven_job_pattern_outranks_a_generic_lexical_tie():
    generic = Candidate('generic', 'Демонтаж теракот', 'м²', quote(TILE_QUOTE, 4).payload)
    result = best_match('Демонтаж на фаянс по стени - санитарни помещения h=2.25 м.',
                        [generic, quote(TILE_QUOTE)], query_unit='м²')
    assert result.is_confident
    assert result.candidate.ref == 'evidence'
    assert result.pool_refs == {'evidence'}


def test_equivalent_screed_job_cohort_ignores_project_thickness_not_original_quotes():
    second = Candidate('second', SCREED_QUOTE.replace('от 2 до 10мм', 'от 10 до 30мм'),
                       'м²', quote(SCREED_QUOTE, 5.5).payload)
    result = best_match('Полагане на саморазливна замазка 2 см по под',
                        [quote(SCREED_QUOTE, 4.6), second], query_unit='м²')
    assert result.is_confident
    assert result.pool_refs == {'evidence', 'second'}
    assert result.median_rate == Decimal('5.05')
    assert second.text.endswith('от 10 до 30мм "Ceresit CN69". крайна цена')


@pytest.mark.parametrize('text', [
    'Доставка и полагане на циментова замазка 2 см по под',
    'Демонтаж на саморазливна замазка 2 см по под',
    'Полагане на саморазливна замазка без материал по под',
])
def test_screed_pattern_keeps_method_operation_and_scope(text):
    query = 'Доставка и полагане на саморазливна замазка 2 см по под'
    result = best_match(query, [quote(text, 4.6)], query_unit='м²')
    assert not result.is_confident


def test_reveal_pattern_binds_installation_to_reveals_not_door_products():
    work = canonical_work(REVEAL_QUOTE)
    assert work.object == 'window_reveal'
    assert work.operations == ('install',)
    assert canonical_work('Обръщане на отвори в стена 25 см').operations == ('install',)
    assert canonical_work('Доставка и монтаж на врата').object == 'door'
    assert not retrieval_plan('Обръщане на отвори в стена 25 см').matches('Доставка и монтаж на врата')


@pytest.mark.parametrize('width', ['40', '25', '12'])
def test_generic_reveal_accepts_quoted_gypsum_board_regardless_of_project_wall_width(width):
    query = f'Обръщане на отвори около прозорци и врати в помещения стена {width}см.'
    expected_width = format((Decimal(width) / 100).normalize(), 'f')
    assert canonical_work(query).contexts['project:dim_len'] == (expected_width,)
    quote_work = canonical_work(REVEAL_QUOTE)
    assert quote_work.contexts['project:width_limit'] == ('до 30см',)
    assert quote_work.contexts['project:dim_len'] == ('0.3',)
    assert retrieval_plan(query).matches(REVEAL_QUOTE)
    assert compare_work(query, REVEAL_QUOTE) == ([], [])
    result = best_match(query, [quote(REVEAL_QUOTE, 20, unit='м', code='OPR3-000159')], query_unit='м')
    assert result.is_confident
    assert suggestion_rate(result.candidate) == Decimal('20')


@pytest.mark.parametrize('query', [
    'Демонтаж на обръщане на отвори около прозорци и врати',
    'Обръщане на отвори с циментова мазилка около прозорци и врати',
    'Обръщане на отвори около прозорци и врати без материали',
])
def test_generic_reveal_rule_does_not_override_explicit_operations_materials_or_scope(query):
    assert not best_match(query, [quote(REVEAL_QUOTE, 12.83, unit='м')], query_unit='м').is_confident


@pytest.mark.parametrize('model', ['150', '510'])
def test_plaster_product_numbers_are_not_inch_connections(model):
    work = canonical_work(f'Направа на варова мазилка по стени, напр. "Рьофикс {model}"')
    assert 'inch_connection' not in work.specs
    assert work.attributes['product'] == (f'рьофикс {model}',)


def test_real_pipe_inches_remain_technical_requirements():
    q = canonical_work('Доставка и монтаж на стоманена тръба 2"')
    assert q.specs['inch_connection'] == ('2',)
    assert score_match('Доставка и монтаж на стоманена тръба 2"',
                       'Доставка и монтаж на стоманена тръба 1"').confidence == 0


@pytest.mark.parametrize(('query', 'text', 'rate', 'variant'), [
    ('Полагане на варова мазилка по стени', WALL_PLASTER, '7.67', ('lime_cement',)),
    ('Полагане на варова мазилка по таван', CEILING_PLASTER, '1.02', ('gypsum', 'lime')),
])
def test_approved_plaster_variants_resolve_to_one_job_without_losing_original_mix(query, text, rate, variant):
    work = canonical_work(text)
    assert work.materials == ('lime',)
    assert work.contexts['material_variant'] == variant
    assert work.concept_ids['materials'] == canonical_work(query).concept_ids['materials']
    assert retrieval_plan(query).matches(text)
    assert compare_work(query, text) == ([], [])
    result = best_match(query, [quote(text, rate)], query_unit='м²')
    assert result.is_confident
    assert suggestion_rate(result.candidate) == Decimal(rate)


@pytest.mark.parametrize('text', [
    'Демонтаж на варова мазилка по стени',
    'Доставка и полагане на декоративна варова мазилка по стени',
    'Доставка и полагане на гипсова мазилка по стени',
    'Доставка и полагане на циментова мазилка по стени',
    'Полагане на варова мазилка без материал по стени',
    'Доставка и полагане на варова мазилка по фасада',
    'Доставка и полагане на финишна варова мазилка по стени',
])
def test_ordinary_plaster_rule_does_not_collapse_different_jobs_or_unapproved_materials(text):
    result = best_match('Полагане на варова мазилка по стени', [quote(text)], query_unit='м²')
    assert not result.is_confident


@pytest.mark.parametrize(('unit', 'source'), [('бр.', 'operator_pricelist'), ('м²', 'reference_web')])
def test_job_equivalence_does_not_bypass_units_or_source_authority(unit, source):
    result = best_match('Полагане на саморазливна замазка 2 см по под',
                        [quote(SCREED_QUOTE, 4.6, unit, source)], query_unit='м²')
    assert not result.is_confident


def test_reveal_operator_rule_selects_only_the_approved_code_not_a_verbatim_rival():
    query = 'Обръщане на отвори около прозорци и врати в помещения стена 40см.'
    pinned = quote(REVEAL_QUOTE, 20, unit='м', code='OPR3-000159')
    rival = Candidate('rival', query, 'м', {**pinned.payload, 'code': 'other', 'unit_rate': '22'})
    result = best_match(query, [rival, pinned], query_unit='м')
    assert result.is_confident
    assert result.candidate is pinned
    assert suggestion_rate(result.candidate) == Decimal('20')
    assert result.pool_refs == {'evidence'}
    assert not result.pool_diverged
    assert any(reason.startswith('operator_pricing_rule:') for reason in result.score.reasons)
    plan = retrieval_plan(query)
    assert plan.candidate_code == 'OPR3-000159'
    from app.modules.cost_match.retrieval_bg import sql_predicate
    assert 'OPR3-000159' in sql_predicate(plan, CostItem).compile().params.values()
    assert best_match(query, [rival], query_unit='м').candidate is None


def test_doors_and_windows_quote_covers_a_door_only_reveal():
    query = 'Обръщане на отвори около врати в помещения стена 25см.'
    assert compare_work(query, REVEAL_QUOTE) == ([], [])
    result = best_match(query, [quote(REVEAL_QUOTE, 20, unit='м', code='OPR3-000159')], query_unit='м')
    assert result.is_confident
    assert result.candidate.payload['code'] == 'OPR3-000159'
    assert retrieval_plan(query).candidate_code == 'OPR3-000159'
    assert compare_work('Обръщане на отвори около прозорци',
                        'Обръщане /доставка и монтаж/ на страници на врати с обикновен гипсокартон')[0]


@pytest.mark.parametrize('change', [
    {'currency': 'USD'}, {'unit_rate': '0'}, {'unit_rate': '-1'},
    {'unit_rate': 'NaN'}, {'unit_rate': 'Infinity'}, {'unit_rate': None},
    {'source': 'reference_web'}, {'auto_pricing_eligible': False},
    {'provenance_status': 'pending_review'},
])
def test_reveal_pin_is_not_an_eligibility_or_currency_override(change):
    candidate = quote(REVEAL_QUOTE, 20, unit='м', code='OPR3-000159')
    candidate = Candidate(candidate.ref, candidate.text, candidate.unit, {**candidate.payload, **change})
    assert not best_match('Обръщане на отвори в стена 25 см', [candidate], query_unit='м').is_confident


@pytest.mark.parametrize('text,unit', [
    (REVEAL_QUOTE, 'м²'),
    ('Доставка и монтаж на врата', 'м'),
    ('Демонтаж на обръщане на отвори', 'м'),
    (REVEAL_QUOTE + ' без демонтаж', 'м'),
    ('Обръщане на отвори без материали', 'м'),
])
def test_reveal_pin_still_checks_the_actual_quotation(text, unit):
    result = best_match('Обръщане на отвори в стена 25 см',
                        [quote(text, 20, unit=unit, code='OPR3-000159')], query_unit='м')
    assert not result.is_confident


def test_grinding_and_hardener_do_not_price_construction_of_a_reinforced_floor():
    query = ('Шлайфана бетонова настилка, d = 50 mm, от бетон C30/37, '
             'максимална фракция на добавъчния материал 8 mm, '
             'положена върху стоманобетонна плоча, свързана с подходящ адхезионен мост. '
             'Армиране със заварена стоманена мрежа Ø4/100/100 mm и микросинтетични '
             'полипропиленови фибри, равномерно разпределени в бетонната смес, мин. 0,9 kg/m³. '
             'Машинно заглаждане и диамантено шлайфане. Обработка със силикатен повърхностен '
             'втвърдител и безцветна защитна импрегнация. Естествен сив цвят, матов финиш. '
             'Машинно изрязване на контролни фуги и запълване с еластичен пълнител.')
    text = 'Шлайфане на бетонова настилка (без материал), полагане на повърхностен втвърдител'
    assert 'fill' in canonical_work(query).operations
    assert 'fill' not in canonical_work(text).operations
    assert not best_match(query, [quote(text, 15.78, code='OPR3-000198')], query_unit='м²').is_confident
