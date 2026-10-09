import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.unit.cost_match.test_work_meaning_v4 import LONG, ROOM, SHORT


@pytest.fixture(autouse=True)
def postgres_configuration(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql+asyncpg://unused@localhost/unused')


def item(text, rate, **metadata):
    return SimpleNamespace(id=uuid.uuid4(), code='quote-' + rate, description=text, descriptions={},
                           unit='м²', rate=Decimal(rate), currency='EUR',
                           source='operator_pricelist', metadata_=metadata)


def service(rows):
    from app.modules.cost_match.service import CostMatchService

    calls = []

    async def find_candidates(description, **kwargs):
        calls.append((description, kwargs))
        return rows

    async def priors(*args):
        return {}

    async def remembered(*args):
        return []

    async def spread(*args, **kwargs):
        return None

    result = CostMatchService(SimpleNamespace())
    result.base_repo = SimpleNamespace(find_candidates=find_candidates)
    result._pattern_priors = priors
    result._remembered_candidates = remembered
    result._price_spread = spread
    return result, calls


def run():
    return SimpleNamespace(id=uuid.uuid4(), candidate_limit=1, cost_source='operator', region='BG',
                           catalog_id=None, source_locale='bg')


def test_actual_service_prices_median_and_keeps_full_original_evidence():
    import json

    scorer, calls = service([item(SHORT, '5.00'), item(LONG, '6.15')])
    result = asyncio.run(scorer._score_line(description=ROOM, unit='м²', source_ref='', run=run()))
    assert result['tier'] == 'high_confidence'
    assert result['suggested_rate'] == Decimal('5.575')
    assert result['suggested_currency'] == 'EUR'
    evidence = result['factors']['equivalent_price_pool']
    assert len(evidence['observations']) == 2
    assert evidence['rate'] == '5.575'
    assert result['factors']['query_work']['work']['defaults_applied']
    assert result['factors']['raw_kcc_description'] == ROOM
    assert calls[0][1]['limit'] == 1
    json.dumps(result['factors'], ensure_ascii=False)


def test_explicit_vat_basis_in_original_quotation_prevents_mixed_median():
    from app.modules.cost_match.service import _to_candidate

    gross = _to_candidate(item(SHORT, '5.00', original_quotation={'money': {'vatIncluded': True}}), 'bg')
    net = _to_candidate(item(LONG, '6.15', vat_included=False), 'bg')
    assert gross.payload['vat_included'] is True
    assert net.payload['vat_included'] is False
    scorer, _ = service([item(SHORT, '5.00', original_quotation={'money': {'vatIncluded': True}}),
                         item(LONG, '6.15', vat_included=False)])
    result = asyncio.run(scorer._score_line(description=ROOM, unit='м²', source_ref='', run=run()))
    assert result['factors']['equivalent_price_pool']['rate'] is None
    assert len(result['factors']['equivalent_price_pool']['observations']) == 1


def test_actual_service_does_not_resurrect_ineligible_exact_quote():
    restricted = 'Поправка на водосточна тръба без демонтаж'
    scorer, calls = service([item(restricted, '5')])
    result = asyncio.run(scorer._score_line(description=restricted, raw_query='Ф25',
                                            unit='м²', source_ref='', run=run()))
    assert result['tier'] == 'unmatched'
    assert result['suggested_rate'] is None
    assert result['alternatives'] == []
    assert calls[0][1]['raw_query'] == 'Ф25'


def test_actual_service_keeps_reference_price_in_review():
    from app.modules.cost_match.service import _to_candidate

    quote = item(LONG, '6.15', origin_kind='reference_web', tenderops_status='pending_review')
    assert not _to_candidate(quote, 'bg').payload['auto_pricing_eligible']
    scorer, _ = service([quote])
    result = asyncio.run(scorer._score_line(description=ROOM, unit='м²', source_ref='', run=run()))
    assert result['tier'] == 'needs_review'
    assert 'reference_price' in result['reason_codes']


def test_actual_service_reports_cross_source_median_but_requires_reference_review():
    scorer, _ = service([item(LONG, '6.15'), item(SHORT, '5.00', origin_kind='reference_web')])
    result = asyncio.run(scorer._score_line(description=ROOM, unit='м²', source_ref='', run=run()))
    assert result['tier'] == 'needs_review'
    assert result['suggested_rate'] == Decimal('5.575')
    assert result['factors']['equivalent_price_pool']['rate'] == '5.575'
    assert result['factors']['reference_price'] is True


def test_raw_kcc_remains_separate_from_effective_parent_context():
    from app.modules.cost_match.schemas import MatchLineInput

    line = MatchLineInput(description='Доставка и монтаж на PPR тръба PN10 Ф25', raw_description='Ф25', unit='м')
    assert line.raw_description == 'Ф25'
    assert line.description != line.raw_description


def test_service_does_not_discard_prices_to_manufacture_a_cohort():
    text = 'Демонтаж на осветителни тела'
    rows = [item(text, str(rate), evidence_origin=f'project-{index}')
            for index, rate in enumerate([100, 10, '10.1', '10.2', '10.3', '10.4'])]
    for row in rows:
        row.unit = 'бр.'
    scorer, _ = service(rows)

    async def spread(*args, **kwargs):
        return {'dup_count': 6, 'dup_rate_min': 10.0, 'dup_rate_max': 100.0,
                'dup_rate_ratio': 10.0}

    scorer._price_spread = spread
    result = asyncio.run(scorer._score_line(description=text, unit='бр.', source_ref='', run=run()))
    assert result['tier'] == 'needs_review'
    evidence = result['factors']['equivalent_price_pool']
    assert evidence['rate'] is None
    assert evidence['min'] == '10' and evidence['max'] == '100'
    assert len(evidence['observations']) == 6
    assert 'price_cohort' not in result['factors'] and 'cohort' not in evidence


def test_service_preserves_no_price_evidence_status_without_a_pool():
    text = 'Монтаж на осветителни тела'
    scorer, _ = service([item('Демонтаж на осветителни тела', '5')])
    result = asyncio.run(scorer._score_line(description=text, unit='м²', source_ref='', run=run()))
    assert result['tier'] not in {'exact', 'high_confidence'}
    assert result['factors']['price_evidence_status'] == 'no_evidence'
