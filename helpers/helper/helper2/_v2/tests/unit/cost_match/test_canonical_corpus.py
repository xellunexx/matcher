from __future__ import annotations

import csv
import gzip
import json
import re
from types import SimpleNamespace

import pytest

from app.modules.cost_match.bulgarian import parse_work
from app.modules.cost_match.matcher import Candidate, HIGH_CONFIDENCE, best_match, score_match
from app.modules.cost_match.work_catalog import catalog, definitions, work_metadata
from app.modules.cost_match.work_context import effective_work, position_contexts
from scripts.enrich_bg_corpus import enrich


def score(query, candidate):
    return score_match(query, candidate, query_unit='м', candidate_unit='м')


@pytest.mark.parametrize(('query', 'candidate'), [
    ('Доставка на кабел СВТ3x1,5mm²', 'Доставка кабелоподобен проводник СВТ 3х1,5мм2'),
    ('Доставка на кабел СВТ3x2,5mm²', 'Доставка кабелоподобен проводник СВТ 3х2,5мм2'),
    ('Доставка на кабел СВТ3x6,0mm²', 'Доставка кабелоподобен проводник СВТ 3х6мм2'),
    ('Доставка на кабел СВТ5x4,0mm²', 'Доставка кабелоподобен проводник СВТ 5х4мм2'),
    ('Полагане на PVC тръба ф50', 'Монтаж на ПВЦ тръба Ø50'),
])
def test_equivalent_notation_shares_meaning_without_changing_prices(query, candidate):
    assert score(query, candidate).confidence >= HIGH_CONFIDENCE
    assert work_metadata(query)['fingerprint'] == work_metadata(candidate)['fingerprint']


@pytest.mark.parametrize('query', [
    'Доставка на кабел СВТ3x2,5mm²',
    'Доставка на кабел СВТ5x1,5mm²',
    'Доставка и монтаж на кабел СВТ3x1,5mm²',
    'Доставка на кабел СВТ3x1,5mm², огнеустойчив',
    'Доставка на кабел СВТ3x1,5mm² в тръба',
])
def test_notation_never_bridges_different_work(query):
    assert score(query, 'Доставка кабелоподобен проводник СВТ 3х1,5мм2').confidence < HIGH_CONFIDENCE


def test_diameter_stays_a_requirement():
    assert score('Полагане на PVC тръба ф63', 'Монтаж на ПВЦ тръба Ø50').confidence == 0


def test_dimension_fragment_is_not_a_verbatim_work_twin():
    assert score('Ф20', 'Ф20').reasons == ['work_context_missing']
    assert score('Ф20', 'Ф20').confidence == 0


def test_explicit_parent_pressure_is_preserved_and_never_replaced():
    query = effective_work('Ф25', ['Полипропиленови тръби за студена вода - PN16'])
    candidate = effective_work('Ф25', ['Полипропиленови тръби за топла вода - PN20'])
    assert parse_work(query).object == 'pipe'
    assert dict(parse_work(query).specs)['pressure'] == {'16'}
    assert score(query, candidate).confidence == 0


def test_compensator_and_pipe_fragments_have_different_meaning():
    query = effective_work('Ф20', ['Точков компенсатор'])
    candidate = effective_work('Ф20', ['Полипропиленови тръби за студена вода - PN16'])
    assert parse_work(query).object == 'compensator'
    assert score(query, candidate).confidence == 0


def test_trade_heading_cannot_supply_work_or_operation():
    assert effective_work('Ф25', ['ЧАСТ ВиК', 'ВОДОПРОВОД']) == 'Ф25'
    text = effective_work('Ф25', ['Полипропиленови тръби - PN16'])
    assert not parse_work(text).operations
    assert parse_work(text).scope is None


def test_unknown_nearest_parent_cannot_borrow_a_different_ancestor_work():
    assert effective_work('Ф25', ['Полипропиленови тръби - PN16', 'Непозната подгрупа']) == 'Ф25'


def test_cyclic_boq_parent_context_is_not_pricing_evidence():
    header = SimpleNamespace(id=1, parent_id=2, unit='section', description='Полипропиленови тръби - PN16')
    child = SimpleNamespace(id=2, parent_id=1, unit='м', description='Ф25')
    assert position_contexts([header, child])[2] == []


def test_boq_parent_tree_preserves_two_different_phi25_rows():
    parents = [SimpleNamespace(id=1, parent_id=None, unit='section', description='Полипропиленови тръби - PN16'),
               SimpleNamespace(id=3, parent_id=None, unit='section', description='Полипропиленови тръби - PN20')]
    children = [SimpleNamespace(id=2, parent_id=1, unit='м', description='Ф25'),
                SimpleNamespace(id=4, parent_id=3, unit='м', description='Ф25')]
    contexts = position_contexts([parents[0], children[0], parents[1], children[1]])
    assert effective_work('Ф25', contexts[2]) != effective_work('Ф25', contexts[4])


@pytest.mark.parametrize(('query', 'candidate'), [
    ('Демонтаж на улуци', 'Демонтаж на водосточни тръби'),
    ('Изготвяне доставка и монтаж на стоманени греди', 'Изготвяне доставка и монтаж на стоманени колони'),
    ('Фасадно скеле - монтаж и демонтаж', 'Монтаж и демонтаж на вътрешно работно скеле'),
    ('Доставка и монтаж на безжичен рутер', 'Доставка и монтаж на безжичен паник бутон'),
])
def test_new_work_families_reject_related_but_distinct_evidence(query, candidate):
    assert score(query, candidate).confidence < HIGH_CONFIDENCE


def test_all_catalog_patterns_compile_and_ids_are_unique():
    assert len(definitions()) == len(catalog()['families'])
    for definition in definitions().values():
        for pattern in definition.object_patterns:
            re.compile(pattern, re.I)
        for rule in definition.notation:
            compiled = re.compile(rule['pattern'], re.I)
            assert set(rule['fields']) <= compiled.groupindex.keys()


def test_enrichment_preserves_every_original_quotation_field(tmp_path):
    original = {'code': 'OPR2-000328', 'description': 'Доставка кабелоподобен проводник СВТ 3х1,5мм2',
                'unit': 'м', 'rate': '0.820000', 'currency': 'EUR', 'source': 'operator_pricelist'}
    corpus = tmp_path / 'corpus.csv'
    with corpus.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(original))
        writer.writeheader()
        writer.writerow(original)
    output = tmp_path / 'corpus.jsonl.gz'
    assert enrich(corpus, output)['records'] == 1
    with gzip.open(output, 'rt', encoding='utf-8') as handle:
        record = json.loads(handle.readline())
    assert record['quotation'] == original
    assert record['canonical_work']['work']['scope'] == 'material'
    assert record['canonical_work']['interpretation_status'] == 'deterministic_unverified'
    assert record['canonical_work']['fingerprint'] == work_metadata('Доставка на кабел СВТ3x1,5mm²')['fingerprint']


def test_metadata_is_not_a_shared_mutable_price_lesson():
    text = 'Доставка на кабел СВТ3x1,5mm²'
    metadata = work_metadata(text)
    metadata['work']['scope'] = 'supply_install'
    assert work_metadata(text)['work']['scope'] == 'material'


def test_equivalent_notation_cannot_hide_conflicting_prices():
    query = 'Доставка на кабел СВТ3x1,5mm²'
    candidates = [Candidate('a', query, 'м', {'unit_rate': '1', 'currency': 'EUR'}),
                  Candidate('b', 'Доставка кабелоподобен проводник СВТ 3х1,5мм2', 'м',
                            {'unit_rate': '5', 'currency': 'EUR'})]
    result = best_match(query, candidates, query_unit='м')
    assert not result.is_confident
    assert result.pool_diverged
    assert result.median_rate is None
    assert 'corpus_price_disagreement' in result.score.reasons


def test_equal_euro_and_lev_quotes_are_not_a_price_conflict():
    query = 'Доставка на кабел СВТ3x1,5mm²'
    candidates = [Candidate('a', query, 'м', {'unit_rate': '10', 'currency': 'EUR'}),
                  Candidate('b', 'Доставка кабелоподобен проводник СВТ 3х1,5мм2', 'м',
                            {'unit_rate': '19.5583', 'currency': 'BGN'})]
    assert best_match(query, candidates, query_unit='м').is_confident
