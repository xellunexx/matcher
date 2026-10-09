import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.cost_match.bulgarian import compare_work
from app.modules.cost_match.matcher import Candidate, best_match
from app.modules.cost_match.work_catalog import canonical_work


@pytest.fixture(autouse=True)
def postgres_configuration(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://unused@localhost/unused')


def offer(run_id, tier='exact', unit='м²', currency='EUR'):
    return SimpleNamespace(id=uuid.uuid4(), run_id=run_id, tier=tier,
                           confidence=Decimal('1') if tier == 'exact' else Decimal('0.9'),
                           suggested_cost_item_id=uuid.uuid4(), suggested_code='price',
                           suggested_description='Боядисване с латекс', suggested_unit=unit,
                           suggested_currency=currency, suggested_rate=Decimal('12'))


@pytest.mark.parametrize('tier,unit,currency', [
    ('needs_review', 'м²', 'EUR'), ('exact', 'м³', 'EUR'), ('exact', 'м²', 'USD'),
])
def test_rejected_same_run_offer_preserves_price_and_evidence_atomically(tier, unit, currency):
    from app.modules.cost_match.service import CostMatchService

    run = SimpleNamespace(id=uuid.uuid4())
    position = SimpleNamespace(unit='м²', quantity='10', unit_rate='10', total='100',
                               price_basis='corpus_exact', confidence='1.0000',
                               metadata_={'cost_match': {'run_id': str(run.id), 'tier': 'exact',
                                                       'confidence': '1', 'code': 'earlier'}})
    scorer = CostMatchService(SimpleNamespace())
    assert scorer._price_position_from_result(position, run, offer(run.id, tier, unit, currency),
                                              project_currency='EUR')
    assert (position.unit_rate, position.total, position.price_basis, position.confidence) == (
        '10', '100', 'corpus_exact', '1.0000')
    assert position.metadata_['cost_match']['code'] == 'earlier'


@pytest.mark.parametrize('tier,expected_basis,expected_rate', [
    ('high_confidence', 'corpus_high', '12.0000'),
    ('needs_review', 'corpus_review', '0'),
])
def test_fresh_run_replaces_or_withdraws_old_automatic_offer(tier, expected_basis, expected_rate):
    from app.modules.cost_match.service import CostMatchService

    run = SimpleNamespace(id=uuid.uuid4())
    position = SimpleNamespace(unit='м²', quantity='10', unit_rate='10', total='100',
                               price_basis='corpus_exact', confidence='1.0000',
                               metadata_={'cost_match': {'run_id': str(uuid.uuid4()), 'tier': 'exact',
                                                       'confidence': '1', 'code': 'stale'}})
    CostMatchService(SimpleNamespace())._price_position_from_result(
        position, run, offer(run.id, tier), project_currency='EUR')
    assert position.unit_rate == expected_rate
    assert position.price_basis == expected_basis
    assert position.metadata_['cost_match']['run_id'] == str(run.id)
    assert position.metadata_['cost_match']['code'] == 'price'


def test_fresh_run_preserves_human_price():
    from app.modules.cost_match.service import CostMatchService

    run = SimpleNamespace(id=uuid.uuid4())
    position = SimpleNamespace(unit='м²', quantity='10', unit_rate='15', total='150',
                               price_basis='corpus_confirmed', confidence='1.0000',
                               metadata_={'cost_match': {'run_id': str(uuid.uuid4()), 'state': 'confirmed',
                                                       'decision': 'confirmed'}})
    CostMatchService(SimpleNamespace())._price_position_from_result(
        position, run, offer(run.id, 'needs_review'), project_currency='EUR')
    assert (position.unit_rate, position.total, position.price_basis) == ('15', '150', 'corpus_confirmed')
    assert position.metadata_['cost_match']['state'] == 'confirmed'


@pytest.mark.parametrize('text,expected', [
    ('Доставка и монтаж на инверторен климатик', 'air_conditioner'),
    ('Доставка и монтаж на инверторен чилър', 'chiller'),
    ('Доставка и монтаж на фотоволтаичен инвертор', 'inverter'),
    ('Доставка и монтаж на инвертори', 'inverter'),
])
def test_inverter_adjective_does_not_replace_priced_hvac_object(text, expected):
    assert canonical_work(text).object == expected


@pytest.mark.parametrize('text,is_floor', [
    ('Монтаж на кабел под мазилка', False),
    ('Поправка на водосточна тръба под корниз', False),
    ('Боядисване по под', True),
    ('Боядисване на пода', True),
    ('Боядисване на подове', True),
    ('Полагане на гранитогрес - под - включително замазка', True),
])
def test_under_preposition_is_not_floor_surface(text, is_floor):
    assert ('floor' in canonical_work(text).locations) == is_floor


def test_included_priming_keeps_standard_paint_defaults_but_not_plain_paint_equivalence():
    text = 'Боядисване с латекс, вкл. грунд'
    work = canonical_work(text)
    assert work.operations == ('install', 'prime')
    assert work.scope == 'supply_install'
    assert work.specs['coats'] == ('2',)
    assert work.defaults_applied
    assert compare_work(text, 'Боядисване с латекс')[0]
    assert not compare_work(text, 'Боядисване с латекс, включително грундиране')[0]
    assert canonical_work('Подмяна на латексова боя').scope is None


def test_confirmed_price_authority_requires_equivalent_work_and_preserves_recent_rulings():
    query = 'Циркулационна помпа'
    def quote(ref, rate, day, text=query):
        return Candidate(ref, text, 'бр.', {'source': 'estimate_confirmed', 'unit_rate': rate,
                                          'currency': 'EUR', 'price_as_of': day})
    result = best_match(query, [quote('old', '500', '2026-09-01'),
                                quote('newA', '1000', '2026-10-01'),
                                quote('newB', '1100', '2026-10-01'),
                                quote('wrong', '200', '2026-10-08', 'Ремонт на циркулационна помпа')],
                        query_unit='бр.')
    assert result.is_confident
    assert result.pool_refs == {'newA', 'newB'}
    assert result.median_rate == Decimal('1050')


def test_masonry_area_and_volume_never_share_a_priced_pool():
    query = 'Изграждане на тухлен зид'
    result = best_match(query, [Candidate('volume', query, 'м³',
                                         {'source': 'operator_pricelist', 'unit_rate': '30', 'currency': 'EUR'})],
                        query_unit='м²')
    assert not result.is_confident
    assert not result.pool_refs
