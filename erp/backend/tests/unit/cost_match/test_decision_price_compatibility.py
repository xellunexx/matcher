import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.cost_match.schemas import MatchDecisionCreate
from app.modules.cost_match.service import (
    BGN_PER_EUR,
    CostMatchService,
    DecisionPayloadError,
    _decision_price,
)


@pytest.fixture(autouse=True)
def postgres_configuration(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://unused@localhost/unused')


@pytest.mark.parametrize('unit,currency', [('pcs', 'EUR'), ('m2', 'USD'), ('m2', '')])
def test_incompatible_prices_are_not_relabelled(unit, currency):
    with pytest.raises(DecisionPayloadError, match='manual price'):
        _decision_price(Decimal('9'), unit, currency, target_unit='m2', project_currency='EUR')


@pytest.mark.parametrize('value', [None, Decimal('0'), Decimal('-1'), Decimal('NaN'), Decimal('Infinity')])
def test_invalid_prices_cannot_be_adopted(value):
    with pytest.raises(DecisionPayloadError, match='finite and positive'):
        _decision_price(value, 'm2', 'EUR', target_unit='m2', project_currency='EUR')


def test_missing_project_currency_cannot_silently_adopt_a_price():
    with pytest.raises(DecisionPayloadError, match='set the project currency'):
        _decision_price(Decimal('9'), 'm2', 'EUR', target_unit='m2', project_currency='')


@pytest.mark.parametrize('unit,target,rate,currency,target_currency,expected', [
    ('m2', 'm²', '5.575', 'EUR', 'EUR', Decimal('5.575')),
    ('t', 'kg', '1000', 'EUR', 'EUR', Decimal('1')),
    ('m2', 'm2', '19.5583', 'BGN', 'EUR', Decimal('10')),
    ('m2', 'm2', '10', 'EUR', 'BGN', Decimal('10') * BGN_PER_EUR),
    ('m2', 'm2', '9', 'USD', 'USD', Decimal('9')),
])
def test_honest_unit_and_fixed_currency_conversions_survive(unit, target, rate, currency, target_currency, expected):
    assert _decision_price(Decimal(rate), unit, currency, target_unit=target,
                           project_currency=target_currency) == expected


@pytest.mark.parametrize('action', ['confirmed', 'overridden', 'manual'])
@pytest.mark.parametrize('unit,currency', [('pcs', 'EUR'), ('m2', 'USD')])
def test_invalid_ruling_has_no_history_pattern_or_state_side_effects(action, unit, currency):
    asyncio.run(_invalid_ruling_scenario(action, unit, currency))


async def _invalid_ruling_scenario(action, unit, currency):
    item_id = uuid.uuid4()
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(currency='EUR')), flush=AsyncMock())
    service = CostMatchService(session)
    service._position_for_result = AsyncMock(return_value=SimpleNamespace(unit='m2'))
    service._record_pattern = AsyncMock()
    service._sync_position_from_decision = AsyncMock()
    service.decision_repo = SimpleNamespace(next_seq=AsyncMock(), create=AsyncMock())
    service.base_repo = SimpleNamespace(get_active=AsyncMock(return_value=SimpleNamespace(
        id=item_id, code='bad', description='offer', descriptions={}, unit=unit, currency=currency, rate='9')))
    run = SimpleNamespace(status='matched', cost_source='operator_pricelist', region=None,
                          catalog_id=None, source_locale='bg')
    result = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), suggested_cost_item_id=item_id,
                             suggested_code='bad', suggested_description='offer', suggested_unit=unit,
                             suggested_currency=currency, suggested_rate=Decimal('9'), factors={}, reason_codes=[],
                             source_description='BOQ', source_unit=unit, decision_state='pending')
    payload = {'decision': action}
    if action == 'overridden':
        payload['cost_item_id'] = item_id
    elif action == 'manual':
        payload.update(rate=Decimal('9'), currency=currency)
    with pytest.raises(DecisionPayloadError, match='manual price'):
        await service.record_decision(run, result, MatchDecisionCreate(**payload), decided_by=uuid.uuid4())
    service.decision_repo.create.assert_not_awaited()
    service.decision_repo.next_seq.assert_not_awaited()
    service._record_pattern.assert_not_awaited()
    service._sync_position_from_decision.assert_not_awaited()
    session.flush.assert_not_awaited()
    assert result.decision_state == 'pending'


@pytest.mark.parametrize('unit,currency', [('pcs', 'EUR'), ('m2', 'USD')])
def test_writeback_defensively_preserves_boq_on_incompatible_decision(unit, currency):
    asyncio.run(_invalid_writeback_scenario(unit, currency))


async def _invalid_writeback_scenario(unit, currency):
    position = SimpleNamespace(unit='m2', quantity='10', unit_rate='0', total='0',
                               boq_id=uuid.uuid4(), metadata_={'cost_match': {}}, price_basis='corpus_review')
    session = SimpleNamespace(get=AsyncMock(side_effect=[SimpleNamespace(is_locked=False, project_id=uuid.uuid4()),
                                                        SimpleNamespace(currency='EUR')]), flush=AsyncMock())
    service = CostMatchService(session)
    service._position_for_result = AsyncMock(return_value=position)
    decision = SimpleNamespace(decision='confirmed', decided_unit=unit, decided_currency=currency,
                               decided_rate=Decimal('9'))
    with pytest.raises(DecisionPayloadError, match='manual price'):
        await service._sync_position_from_decision(SimpleNamespace(), SimpleNamespace(), decision)
    assert (position.unit_rate, position.total, position.price_basis) == ('0', '0', 'corpus_review')
    assert position.metadata_ == {'cost_match': {}}
    session.flush.assert_not_awaited()
