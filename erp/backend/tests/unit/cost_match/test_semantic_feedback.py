import asyncio
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.modules.cost_match import semantic_feedback
from app.modules.cost_match.schemas import MatchDecisionCreate, SemanticChoice
from app.modules.cost_match.semantic_feedback import VERSION, decode_link, encode_link, semantic_links

QUERY = 'Разваляне на 25 см. тухлен зид'
QUOTATION = 'Очукване на издадени части от тухлена зидария, вкл. натоварване и извозване на отпадъците'


def test_review_pairs_action_object_and_scope_without_preapproving_any_link():
    links = {link['role']: link for link in semantic_links(QUERY, QUOTATION)}
    assert (links['action']['query'], links['action']['candidate']) == ('Разваляне', 'Очукване')
    assert (links['object']['query'], links['object']['candidate']) == ('тухлен зид', 'тухлена зидария')
    assert 'издадени части' in links['scope']['candidate']
    assert links['scope']['query'] == '—'
    assert all('verdict' not in link for link in links.values())


def test_substrate_is_not_primary_plaster_material():
    links = {link['role']: link for link in semantic_links(
        'Очукване на мазилка от тухлена стена', 'Очукване на мазилка от бетонова стена')}
    assert links['substrate']['query'] == 'тухлена'
    assert links['substrate']['candidate'] == 'бетонова'
    assert 'material' not in links


def test_links_invalidate_on_changed_quotation_query_or_rule_catalog(monkeypatch):
    first = semantic_links(QUERY, QUOTATION)[0]['id']
    assert first != semantic_links(QUERY + ' с извозване', QUOTATION)[0]['id']
    assert first != semantic_links(QUERY, QUOTATION + ' до 5 км')[0]['id']
    monkeypatch.setattr(semantic_feedback, 'catalog_digest', lambda: 'new-rule-catalog')
    assert first != semantic_links(QUERY, QUOTATION)[0]['id']


def test_structured_marker_roundtrips_but_generic_shared_words_are_not_lessons():
    link = semantic_links(QUERY, QUOTATION)[0]
    encoded = encode_link(link, 'different', 'OPR2-000030', 'м²')
    assert decode_link(encoded) == {key: value for key, value in {
        **link, 'verdict': 'different', 'code': 'OPR2-000030', 'unit': 'м²',
    }.items() if key != 'label'}


@pytest.mark.parametrize('value', [None, 1, 'тухла', VERSION + '{}', VERSION + '[]',
                                 VERSION + 'not-json', VERSION + '{"verdict":"same"}'])
def test_malformed_or_legacy_markers_are_ignored(value):
    assert decode_link(value) is None


@pytest.mark.parametrize('payload', [
    {'link_id': '123', 'verdict': 'same'},
    {'link_id': 'z' * 24, 'verdict': 'same'},
    {'link_id': 'a' * 24, 'verdict': 'maybe'},
])
def test_invalid_link_payloads_are_rejected(payload):
    with pytest.raises(ValidationError):
        SemanticChoice(**payload)


@pytest.mark.parametrize('case', ['duplicate', 'unknown', 'wrong-item', 'confirm-different', 'manual'])
def test_invalid_review_does_not_write_history(case, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://unused@localhost/unused')
    from app.modules.cost_match.service import CostMatchService, DecisionPayloadError

    async def scenario():
        item_id = uuid.uuid4()
        active = SimpleNamespace(id=item_id, code='OPR2-000030', description=QUOTATION,
                                 descriptions={}, unit='м²', rate=Decimal('4.6'), currency='EUR',
                                 source='operator_pricelist', metadata_={})
        session = SimpleNamespace(flush=AsyncMock())
        service = CostMatchService(session)
        service.base_repo = SimpleNamespace(get_active=AsyncMock(return_value=active))
        service.decision_repo = SimpleNamespace(next_seq=AsyncMock(), create=AsyncMock())
        run = SimpleNamespace(status='matched', cost_source='operator', region=None,
                              catalog_id=None, source_locale='bg')
        result = SimpleNamespace(id=uuid.uuid4(), suggested_cost_item_id=item_id, factors={},
                                 suggested_code=active.code, suggested_description=QUOTATION,
                                 suggested_unit=active.unit, suggested_rate=active.rate,
                                 suggested_currency='EUR', source_description=QUERY,
                                 source_unit=active.unit, reason_codes=[], decision_state='pending')
        choice = {'link_id': semantic_links(QUERY, QUOTATION)[0]['id'], 'verdict': 'same'}
        data = {'decision': 'rejected', 'semantic_choices': [choice]}
        if case == 'duplicate':
            data['semantic_choices'].append(choice.copy())
        elif case == 'unknown':
            choice['link_id'] = 'a' * 24
        elif case == 'wrong-item':
            choice['cost_item_id'] = uuid.uuid4()
        elif case == 'confirm-different':
            data['decision'] = 'confirmed'
            choice['verdict'] = 'different'
        else:
            data.update(decision='manual', rate=Decimal('4.6'))
        with pytest.raises(DecisionPayloadError):
            await service.record_decision(run, result, MatchDecisionCreate(**data), decided_by=uuid.uuid4())
        service.decision_repo.create.assert_not_awaited()
        service.decision_repo.next_seq.assert_not_awaited()
        session.flush.assert_not_awaited()
        assert result.decision_state == 'pending'

    asyncio.run(scenario())


def test_learning_records_only_explicit_choices_and_no_price(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://unused@localhost/unused')
    from app.modules.cost_match.service import CostMatchService

    async def scenario():
        captured = []
        service = CostMatchService(SimpleNamespace(add_all=captured.extend, flush=AsyncMock()))
        result = SimpleNamespace(id=uuid.uuid4(), run_id=uuid.uuid4(), project_id=uuid.uuid4(),
                                 factors={}, source_description=QUERY)
        decision = SimpleNamespace(id=uuid.uuid4(), decision='confirmed')
        item = SimpleNamespace(id=uuid.uuid4(), code='OPR2-000030', unit='м²',
                               description=QUOTATION, descriptions={})
        await service._record_pattern(SimpleNamespace(), result, decision, [])
        assert not captured
        link = semantic_links(QUERY, QUOTATION)[0]
        await service._record_pattern(SimpleNamespace(), result, decision, [(link, 'same', item)])
        assert len(captured) == 1
        pattern = captured[0]
        assert pattern.query_text == QUERY and pattern.candidate_text == QUOTATION
        assert pattern.cost_item_id == item.id
        assert len(pattern.shared_tokens) == 1
        assert decode_link(pattern.shared_tokens[0])['verdict'] == 'same'
        assert 'rate' not in json.loads(pattern.shared_tokens[0][len(VERSION):])

    asyncio.run(scenario())


@pytest.mark.parametrize(('query', 'quotation', 'unit', 'candidate_unit'), [
    ('Демонтаж на осветителни тела', 'Демонтаж на осветителни тела', 'бр.', 'м²'),
    ('Демонтаж на осветителни тела', 'Демонтаж на осветителни тела, без демонтаж', 'бр.', 'бр.'),
    ('Изпълнение на шлайфана бетонова настилка 50 мм C30/37 с армировка',
     'Шлайфане на бетонова настилка (без материал), полагане на повърхностен втвърдител', 'м²', 'м²'),
    (QUERY, QUOTATION, 'м²', 'м²'),
])
def test_positive_prior_cannot_override_unit_exclusion_or_partial_work(query, quotation, unit, candidate_unit):
    from app.modules.cost_match.matcher import Candidate, best_match

    candidate = Candidate('quotation', quotation, candidate_unit, {
        'pattern_prior': 1.25, 'rate': '5', 'currency': 'EUR', 'source': 'operator_pricelist',
    })
    assert not best_match(query, [candidate], query_unit=unit).is_confident
