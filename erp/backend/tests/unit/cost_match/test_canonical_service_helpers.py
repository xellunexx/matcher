"""Exercise actual pure service functions without claiming a DB integration test."""

from __future__ import annotations

import ast
import asyncio
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.modules.cost_match import matcher


@pytest.fixture
def helpers():
    source = Path(__file__).resolve().parents[3] / 'app/modules/cost_match/service.py'
    parsed = ast.parse(source.read_text(encoding='utf-8-sig'))
    names = {'_parse_rate', '_localized_description', '_to_candidate',
             '_quantise_confidence', '_candidate_snapshot', '_dedupe_pool'}
    nodes = ast.parse('from __future__ import annotations').body
    nodes += [node for node in parsed.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == '_REFERENCE_ORIGINS'
                      for target in node.targets)]
    nodes += [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names]
    service = next(node for node in parsed.body if isinstance(node, ast.ClassDef) and node.name == 'CostMatchService')
    nodes += [node for node in service.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_score_line']
    namespace = dict(vars(matcher))
    namespace.update({'Decimal': Decimal, 'InvalidOperation': InvalidOperation, 'uuid': uuid,
                      'BGN_PER_EUR': Decimal('1.95583'), '_CONFIDENCE_PLACES': Decimal('0.0001'),
                      'TIER_UNMATCHED': 'unmatched'})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


def item(description, metadata=None):
    return SimpleNamespace(id=uuid.uuid4(), code='OPR-test', description=description,
                           descriptions={}, metadata_=metadata or {}, unit='м',
                           rate='0.820000', currency='EUR', source='operator_pricelist')


def test_corpus_builder_keeps_original_quote_and_does_not_score_unverified_aliases(helpers):
    quote = item('Доставка кабелоподобен проводник СВТ 3х1,5мм2',
                 {'bill_terms': {'bg': 'Доставка и монтаж на вентилатор'}})
    candidate = helpers['_to_candidate'](quote, 'bg')
    assert candidate.text == quote.description
    assert candidate.payload['display_text'] == quote.description
    assert candidate.payload['unit_rate'] == '0.820000'
    assert candidate.payload['quotation_rate'] == '0.820000'
    score = matcher.score_match(candidate.text, candidate.text, query_unit='м', candidate_unit='м')
    snapshot = helpers['_candidate_snapshot'](candidate, score)
    assert snapshot['canonical_work'] == candidate.payload['canonical_work']
    assert snapshot['description'] == quote.description
    assert snapshot['quotation_rate'] == '0.820000'


def test_quote_context_is_separate_from_original_wording_and_price(helpers):
    quote = item('Ф25', {'work_context': ['Полипропиленови тръби - PN16']})
    candidate = helpers['_to_candidate'](quote, 'bg')
    assert candidate.payload['display_text'] == 'Ф25'
    assert candidate.text == 'Полипропиленови тръби - PN16; Ф25'
    assert candidate.payload['unit_rate'] == quote.rate
    assert candidate.payload['canonical_work']['work']['specs']['pressure'] == ('16',)


def test_lev_to_euro_keeps_unconverted_quotation(helpers):
    quote = item('Доставка на кабел СВТ3x1,5mm²')
    quote.currency = 'BGN'
    quote.rate = '19.5583'
    candidate = helpers['_to_candidate'](quote, 'bg')
    assert Decimal(candidate.payload['unit_rate']) == Decimal('10')
    assert candidate.payload['currency'] == 'EUR'
    assert candidate.payload['quotation_rate'] == '19.5583'
    assert candidate.payload['quotation_currency'] == 'BGN'


def test_bg_consolidation_cannot_erase_conflicting_price_evidence(helpers):
    quotes = [item('Доставка на кабел СВТ3x1,5mm²') for _ in range(3)]
    quotes[1].rate = '100'
    assert helpers['_dedupe_pool'](quotes, 'bg') == quotes


@pytest.mark.parametrize('description', ['Ф20', 'Ф25', 'Ø50'])
def test_unqualified_fragment_stops_before_db_or_model_retrieval(helpers, description):
    result = asyncio.run(helpers['_score_line'](None, description=description,
                                              unit='м', source_ref='', run=None))
    assert result['tier'] == 'unmatched'
    assert result['suggested_rate'] is None
    assert result['reason_codes'] == ['work_context_missing']
