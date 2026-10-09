import asyncio
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.cost_match.models import DECISION_PENDING, RUN_STATUS_MATCHED
from app.modules.cost_match.service import CostMatchService, DecisionPayloadError
from app.modules.projects.models import Project


@pytest.fixture(autouse=True)
def postgres_configuration(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://unused@localhost/unused')


def fixture():
    ids = [uuid.uuid4() for _ in range(3)]
    results = [SimpleNamespace(line_no=i + 1, decision_state=DECISION_PENDING,
                               suggested_cost_item_id=uuid.uuid4()) for i in range(3)]
    positions = [SimpleNamespace(id=id_, price_basis='corpus_review', unit_rate='0') for id_ in ids]
    run = SimpleNamespace(id=uuid.uuid4(), status=RUN_STATUS_MATCHED,
                          notes=json.dumps({'positions': [str(id_) for id_ in ids]}))
    service = CostMatchService(SimpleNamespace(get=AsyncMock(return_value=Project(currency='EUR'))))
    service.run_repo = SimpleNamespace(list_for_boq=AsyncMock(return_value=[run]))
    service.result_repo = SimpleNamespace(list_all_for_run=AsyncMock(return_value=results))

    async def position_for_result(run, result):
        return positions[result.line_no - 1]

    async def record_decision(run, result, data, *, decided_by, propagate_duplicates=True):
        targets = results if propagate_duplicates else [result]
        for target in targets:
            target.decision_state = data.decision
            positions[target.line_no - 1].price_basis = 'corpus_confirmed'
            positions[target.line_no - 1].unit_rate = '9'

    service._position_for_result = position_for_result
    service.record_decision = AsyncMock(side_effect=record_decision)
    service._learn_confirmed_price = AsyncMock(return_value=False)
    boq = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    return service, boq, ids, results, positions


def test_bulk_counts_positions_not_propagated_groups():
    asyncio.run(_bulk_count_scenario())


async def _bulk_count_scenario():
    service, boq, _, results, positions = fixture()
    counts = await service.confirm_boq_positions(boq, position_ids=None, decided_by=uuid.uuid4())
    assert counts == {'confirmed': 3, 'skipped': 0, 'learned': 0}
    assert all(result.decision_state == 'confirmed' for result in results)
    assert all(position.unit_rate == '9' for position in positions)
    assert service.record_decision.await_count == 3
    for call in service.record_decision.await_args_list:
        assert call.kwargs['propagate_duplicates'] is False


def test_selected_confirmation_does_not_adopt_unselected_duplicates():
    asyncio.run(_selected_scenario())


async def _selected_scenario():
    service, boq, ids, results, _ = fixture()
    counts = await service.confirm_boq_positions(boq, position_ids=[ids[0]], decided_by=uuid.uuid4())
    assert counts == {'confirmed': 1, 'skipped': 0, 'learned': 0}
    assert [result.decision_state for result in results] == ['confirmed', DECISION_PENDING, DECISION_PENDING]


def test_invalid_row_is_skipped_without_stopping_compatible_rows():
    asyncio.run(_invalid_row_scenario())


async def _invalid_row_scenario():
    service, boq, _, results, positions = fixture()
    valid = service.record_decision.side_effect

    async def record_decision(run, result, data, **kwargs):
        if result is results[1]:
            raise DecisionPayloadError('incompatible unit')
        return await valid(run, result, data, **kwargs)

    service.record_decision.side_effect = record_decision
    counts = await service.confirm_boq_positions(boq, position_ids=None, decided_by=uuid.uuid4())
    assert counts == {'confirmed': 2, 'skipped': 1, 'learned': 0}
    assert results[1].decision_state == DECISION_PENDING
    assert positions[1].unit_rate == '0'


def test_each_operator_learning_gate_receives_its_pre_ruling_evidence():
    asyncio.run(_learning_scenario())


async def _learning_scenario():
    service, boq, _, _, positions = fixture()
    for position in positions:
        position.price_basis = 'quotation'
        position.unit_rate = '9'
    service._learn_confirmed_price.return_value = True
    counts = await service.confirm_boq_positions(boq, position_ids=None, decided_by=uuid.uuid4())
    assert counts == {'confirmed': 3, 'skipped': 0, 'learned': 3}
    assert service._learn_confirmed_price.await_count == 3
    for call in service._learn_confirmed_price.await_args_list:
        assert call.kwargs['declared_basis'] == 'quotation'
        assert call.kwargs['declared_rate'] == Decimal('9')


@pytest.mark.parametrize('state', ['pending', 'confirmed', 'manual', 'rejected'])
@pytest.mark.parametrize('selected', [False, True])
def test_latest_results_never_fall_back_to_old_pending_generations(state, selected):
    asyncio.run(_latest_generation_scenario(state, selected))


async def _latest_generation_scenario(state, selected, *, closed=False):
    service, boq, ids, results, positions = fixture()
    for result in results:
        result.decision_state = state
    latest = service.run_repo.list_for_boq.return_value[0]
    if closed:
        latest.status = 'closed'
    old = SimpleNamespace(id=uuid.uuid4(), status=RUN_STATUS_MATCHED, notes=latest.notes)
    old_results = [SimpleNamespace(line_no=i + 1, decision_state=DECISION_PENDING,
                                   suggested_cost_item_id=uuid.uuid4()) for i in range(3)]
    service.run_repo.list_for_boq.return_value = [latest, old]
    service.result_repo.list_all_for_run.side_effect = [results, old_results]
    counts = await service.confirm_boq_positions(boq, position_ids=[ids[0]] if selected else None,
                                                decided_by=uuid.uuid4())
    expected = 1 if selected else 3
    adopts_latest = state == DECISION_PENDING and not closed
    assert counts == {'confirmed': expected if adopts_latest else 0,
                      'skipped': expected if selected and not adopts_latest else 0, 'learned': 0}
    assert all(result.decision_state == DECISION_PENDING for result in old_results)
    for call in service.record_decision.await_args_list:
        assert call.args[0] is latest
    if not adopts_latest:
        assert all(position.unit_rate == '0' for position in positions)


def test_closed_latest_run_cannot_resurrect_old_pending_prices():
    asyncio.run(_latest_generation_scenario(DECISION_PENDING, True, closed=True))
