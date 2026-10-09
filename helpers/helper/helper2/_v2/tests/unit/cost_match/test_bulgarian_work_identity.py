import pytest

from app.modules.cost_match.bulgarian import compare_work, parse_work
from app.modules.cost_match.matcher import HIGH_CONFIDENCE, Candidate, best_match, score_match


@pytest.mark.parametrize(('query', 'candidate', 'unit'), [
    ('Кръгъл канален вентилатор Vсм=300 m³/h Р=150 Pa', 'Callistemon laevis. Калистемон H=125-150', 'бр'),
    ('Кръгъл канален вентилатор Vсм=300 m³/h Р=150 Pa', 'Callistemon laevis H=125-150', 'бр'),
    ('Разваляне на 25 см. тухлен зид', 'Направа и разваляне на кофраж за стени', 'м2'),
    ('Полагане на варова мазилка по стени', 'Направа на гипсова мазилка по стени', 'м2'),
    ('Полагане на гипсова шпакловка по стени', 'Направа на гипсова мазилка по стени', 'м2'),
    ('Полагане на латекс по стени включително грундиране', 'Направа на гипсова мазилка по стени', 'м2'),
    ('Шлайфана бетонова настилка d=50mm C30/37', 'Направа на замазка за основа на настилки', 'м2'),
    ('Доставка и монтаж на кръгъл въздуховод Ø160 вкл. фасонни части', 'Доставка и монтаж на спирален въздуховод Ø 400', 'м'),
    ('Мрежест филтър ф2``', 'Доставка и монтаж на мрежест филтър ∅1"', 'бр'),
    ('Доставка и монтаж на конусен смукател DVS100', 'Помпа с RDM/DMX управление, смукател 1 1/2"', 'бр'),
    ('Локално пречиствателно съоръжение 1,2 m3/d включително изкоп и засипване', 'Циркулационна помпа 6,2 m3/h', 'бр'),
])
def test_reported_wrong_work_is_rejected_even_with_prior(query, candidate, unit):
    score = score_match(query, candidate, query_unit=unit, candidate_unit=unit, prior=100)
    assert score.confidence == 0
    assert any(reason.startswith('work_') for reason in score.reasons)


@pytest.mark.parametrize('header', ['СМР-РАЗПРЕДЕЛЕНИЕ КОТА +0.80', 'СМР- РАЗПРЕДЕЛЕНИЕ КОТА +3.60', 'Общо СМР'])
def test_header_cannot_match_even_itself(header):
    assert score_match(header, header, query_unit='м2', candidate_unit='м2').confidence == 0


def test_included_work_is_not_boilerplate():
    score = score_match('Доставка и монтаж въздуховод ф160, вкл. фасонни части',
                        'Доставка и монтаж въздуховод ф160', query_unit='м', candidate_unit='м')
    assert score.factors['exact'] == 0
    assert score.confidence < HIGH_CONFIDENCE
    assert 'work_inclusions_incomplete' in score.reasons


def test_bundle_requires_all_operations():
    q = 'Демонтаж и обратен монтаж на радиатор'
    c = 'Демонтаж на радиатор'
    assert parse_work(q).operations == {'demolish', 'reinstall'}
    assert score_match(q, c, query_unit='бр', candidate_unit='бр').confidence == 0


def test_accessory_is_not_the_host_object():
    assert parse_work('Доставка и монтаж на скоба за вентилатор').object == 'fastener'
    assert parse_work('Доставка и монтаж на вентилатор с крепежи').object == 'fan'
    assert compare_work('Доставка и монтаж на скоба за вентилатор', 'Доставка и монтаж вентилатор')[0]


def test_included_secondary_object_is_not_primary_object():
    frame = parse_work('Полагане на гранит-стълби включително грундиране и замазка')
    assert frame.object == 'granite_stairs'
    assert 'prime' in frame.operations


def test_unstated_supply_is_not_invented():
    assert parse_work('Монтаж на радиатор').scope is None
    assert 'work_scope_unknown' in compare_work('Монтаж на радиатор', 'Доставка и монтаж радиатор')[1]


def test_missing_specs_need_review_despite_huge_prior():
    score = score_match('Доставка и монтаж на въздуховод ф160', 'Доставка и монтаж на въздуховод',
                        query_unit='м', candidate_unit='м', prior=100)
    assert score.confidence < HIGH_CONFIDENCE
    assert 'work_spec_incomplete:diameter' in score.reasons


def test_numbers_alone_are_not_object_evidence():
    score = score_match('Кръгъл канален вентилатор Р=150 Pa', 'Калистемон H=150',
                        query_unit='бр', candidate_unit='бр', prior=100)
    assert score.confidence == 0


def test_different_measurement_dimensions_never_reach_high_tier_with_prior():
    score = score_match('Зидане на отвори с тухла', 'Иззиждане на отвори с тухла',
                        query_unit='м³', candidate_unit='м²', prior=100)
    assert score.confidence < HIGH_CONFIDENCE


def test_conflicting_exclusion_cannot_be_hidden_by_stopwords():
    score = score_match('Доставка и монтаж на мазилка без грунд', 'Доставка и монтаж на мазилка вкл грунд',
                        query_unit='м2', candidate_unit='м2')
    assert score.confidence == 0


def test_unknown_scope_and_words_do_not_enter_price_pool():
    result = best_match('Доставка и монтаж на тръба ф50 вкл изолация', [
        Candidate('partial', 'Доставка и монтаж на тръба ф50', 'м', {'unit_rate': '10', 'currency': 'EUR'}),
    ], query_unit='м')
    assert not result.is_confident
    assert result.median_rate is None


def test_genuine_paraphrase_can_be_confident_without_llm():
    score = score_match('Полагане на варова мазилка по стени', 'Направа на варова мазилка по стени',
                        query_unit='м2', candidate_unit='м2')
    assert HIGH_CONFIDENCE <= score.confidence < 1
    assert score.factors['work_compatible'] == 1


def test_same_material_different_location_is_a_conflict():
    score = score_match('Полагане на варова мазилка по стени', 'Направа на варова мазилка по тавани',
                        query_unit='м2', candidate_unit='м2')
    assert score.confidence == 0
    assert 'work_location_conflict' in score.reasons


def test_painting_and_laying_paint_share_the_positive_operation():
    assert parse_work('Боядисване с латексова боя по стени').operations == {'install'}
    assert parse_work('Бордюр - включително изкоп и бетониране').operations == {'excavate', 'install'}


def test_exact_learned_volume_row_keeps_real_price():
    result = best_match('Иззиждане на отвори с тухла', [
        Candidate('learned', 'Иззиждане на отвори с тухла', 'м³', {'unit_rate': '8.0000'}),
    ], query_unit='м³')
    assert result.is_confident
    assert result.score.confidence == 1


def test_inch_fraction_equivalence_and_invalid_fraction():
    a = dict(parse_work('Мрежест филтър 1 1/2"').specs)['inch_connection']
    b = dict(parse_work('Мрежест филтър 1,5"').specs)['inch_connection']
    assert a == b == {'1.5'}
    parse_work('Мрежест филтър 1 1/0"')
