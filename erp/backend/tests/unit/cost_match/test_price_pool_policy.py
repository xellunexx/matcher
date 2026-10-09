from decimal import Decimal

import pytest

from app.modules.cost_match.matcher import Candidate, best_match
from app.modules.cost_match.work_catalog import evidence_origin

JOB = 'Демонтаж на осветителни тела'


def quotes(rates):
    return [Candidate(str(index), JOB, 'бр.', {
        'unit_rate': str(rate), 'currency': 'EUR', 'source': 'operator_pricelist',
        'auto_pricing_eligible': True,
    }) for index, rate in enumerate(rates)]


@pytest.mark.parametrize(('high', 'median'), [('12.3', '11.15'), ('15', '12.5')])
def test_twenty_percent_does_not_block_semantically_equivalent_prices(high, median):
    result = best_match(JOB, quotes([10, high]), query_unit='бр.')
    assert result.is_confident and not result.pool_diverged
    assert result.median_rate == Decimal(median)
    assert result.pool_size == 2 and result.pool_refs == {'0', '1'}


def test_previous_large_disagreement_gate_is_preserved():
    result = best_match(JOB, quotes([10, '15.000001']), query_unit='бр.')
    assert not result.is_confident and result.pool_diverged
    assert result.median_rate is None


def test_five_close_prices_do_not_suppress_the_remaining_evidence():
    result = best_match(JOB, quotes([100, 10, '10.1', '10.2', '10.3', '10.4']), query_unit='бр.')
    assert not result.is_confident and result.pool_diverged
    assert result.median_rate is None
    assert result.pool_size == 6 and result.pool_refs == {'0', '1', '2', '3', '4', '5'}


def test_removing_the_cohort_does_not_remove_reference_review():
    rows = quotes([10, '12.3'])
    rows[1].payload['auto_pricing_eligible'] = False
    result = best_match(JOB, rows, query_unit='бр.')
    assert result.median_rate == Decimal('11.15')
    assert not result.is_confident and result.score.factors['reference_price']


def test_prices_do_not_compensate_for_a_different_action():
    result = best_match('Монтаж на осветителни тела', quotes([10, 11]), query_unit='бр.')
    assert not result.is_confident and result.median_rate is None


@pytest.mark.parametrize(('metadata', 'origin'), [
    ({'evidence_origin': 'project-1'}, 'project-1'),
    ({'original_quotation': {'origin': {'ref': ' project-2 '}}}, 'project-2'),
    ({'source_boq': 'project-3'}, 'project-3'),
    ({}, ''),
])
def test_source_provenance_is_preserved_without_price_clustering(metadata, origin):
    assert evidence_origin(metadata) == origin
