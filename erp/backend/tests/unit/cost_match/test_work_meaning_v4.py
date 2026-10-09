import gzip
import json
from copy import deepcopy
from decimal import Decimal

import pytest

from app.modules.cost_match.bulgarian import compare_work, parse_work
from app.modules.cost_match.matcher import Candidate, best_match, score_match
from app.modules.cost_match.retrieval_bg import retrieval_plan
from app.modules.cost_match.work_catalog import canonical_work, catalog, work_metadata

SHORT = 'Боядисване с латекс едноцветно · две ръце'
LONG = 'Боядисване /доставка и полагане/ двукратно с латексова боя по стени и тавани, цвят бял, "Леко Интерин" на "Оргахим"АД. крайна цена'
ROOM = 'Боядисване с латекс в санитарни помещения'


def candidate(ref, description, rate='5', unit='м²', currency='EUR', **extra):
    return Candidate(ref, description, unit, {'unit_rate': rate, 'currency': currency, **extra})


@pytest.mark.parametrize('query', [
    ROOM, SHORT, 'Боядисване с латекс едноцветно · 2 слоя',
    'Боядисване с латекс по стени', 'Боядисване с латекс по тавани',
    'Боядисване с латекс едноцветно · два слоя',
    'Боядисване с латекс едноцветно · 2 ръце',
    'Полагане на латекс в санитарни помещения',
    'Доставка и полагане на латексова боя в санитарни помещения',
    'Боядисване /доставка и полагане/ двукратно с латексова боя по стени и тавани',
])
def test_paint_shorthand_is_proven_meaning_not_lexical_overlap(query):
    result = best_match(query, [candidate('long', LONG, '6.15')], query_unit='м²', locale='bg')
    assert result.is_confident
    assert result.score.factors['semantic_work_equivalent']
    assert not compare_work(query, LONG)[0]
    assert not compare_work(query, LONG)[1]


@pytest.mark.parametrize('query', [
    'Боядисване с латекс само труд по стени и тавани',
    'Доставка на латексова боя',
    'Боядисване с блажна боя по стени и тавани',
    'Боядисване с латекс едноцветно · една ръка',
    'Боядисване с латекс едноцветно · три ръце',
    'Боядисване с латекс двуцветно · две ръце',
    'Боядисване с влагоустойчив латекс по стени и тавани',
    'Боядисване с латекс по стени и тавани, цвят черен',
    'Боядисване с латекс по фасада',
    'Боядисване с латекс по пода',
    'Боядисване с латекс по врати',
    'Боядисване с латекс Dulux по стени и тавани',
    'Боядисване с латекс със специален ефект',
    'Полагане на кабел в санитарни помещения',
])
def test_paint_near_misses_remain_unpriced(query):
    assert not best_match(query, [candidate('long', LONG)], query_unit='м²', locale='bg').is_confident


def test_profile_and_optional_details_are_auditable():
    q, c = canonical_work(SHORT), canonical_work(LONG)
    assert q.scope == c.scope == 'supply_install'
    assert q.materials == c.materials == ('latex',)
    assert q.specs == c.specs == {'coats': ('2',), 'colour_count': ('1',)}
    assert q.concept_ids == c.concept_ids
    assert q.defaults_applied and not c.defaults_applied
    assert c.attributes['colour'] == ('white',)
    assert c.attributes['manufacturer'] == ('orgachim',)
    assert c.attributes['product'] == ('leko_interin',)
    assert canonical_work(ROOM).contexts['room'] == ('room', 'sanitary')


def test_disabling_profile_does_not_invent_scope_or_coats(monkeypatch):
    from app.modules.cost_match import work_catalog

    original = deepcopy(catalog())
    original['profiles'] = []
    monkeypatch.setattr(work_catalog, 'catalog', lambda: original)
    canonical_work.cache_clear()
    try:
        assert not best_match(ROOM, [candidate('long', LONG)], query_unit='м²').is_confident
        assert not canonical_work(ROOM).defaults_applied
    finally:
        canonical_work.cache_clear()


def test_price_variation_produces_decimal_median_even_with_verbatim_winner():
    pool = [candidate('short', SHORT, '5.00', 'M2'), candidate('long', LONG, '6.15')]
    original = deepcopy(pool)
    for query in (SHORT, ROOM):
        for quotes in (pool, list(reversed(pool))):
            outcome = best_match(query, quotes, query_unit='м²', locale='bg')
            assert outcome.is_confident
            assert outcome.median_rate == Decimal('5.575')
            assert outcome.pool_min == Decimal('5.00')
            assert outcome.pool_max == Decimal('6.15')
            assert outcome.pool_refs == {'short', 'long'}
            assert outcome.pool_size == 2
    assert pool == original


def test_price_pool_does_not_admit_cheap_wrong_work():
    invalid = [candidate('oil', LONG.replace('латексова', 'блажна'), '5.50'),
               candidate('coats', LONG.replace('двукратно', 'трикратно'), '5.50'),
               candidate('scope', LONG.replace('/доставка и полагане/', 'само труд'), '5.50'),
               candidate('unit', LONG, '5.50', 'кг'), candidate('currency', LONG, '5.50', currency='USD')]
    result = best_match(ROOM, [candidate('short', SHORT), candidate('long', LONG, '6.15'), *invalid], query_unit='м²')
    assert result.median_rate == Decimal('5.575')
    assert result.pool_refs == {'short', 'long'}


def test_materially_divergent_prices_still_require_review():
    result = best_match(ROOM, [candidate('short', SHORT), candidate('long', LONG, '50')], query_unit='м²')
    assert not result.is_confident
    assert result.pool_diverged
    assert result.median_rate is None
    assert 'corpus_price_disagreement' in result.score.reasons


def test_median_uses_converted_query_units():
    query = 'Подмяна на водосточна тръба'
    result = best_match(query, [candidate('metre', query, '5', 'м'),
                                candidate('kilometre', query, '6000', 'км')], query_unit='м')
    assert result.is_confident
    assert result.median_rate == Decimal('5.5')


def test_median_currency_and_tax_bases_are_not_mixed():
    result = best_match(ROOM, [candidate('a', SHORT, '5', vat_included=True),
                              candidate('b', LONG, '6.15', vat_included=False)], query_unit='м²')
    assert result.pool_size == 1
    query = 'Доставка и монтаж на PPR тръба Ф25'
    result = best_match(query, [candidate('eur', query, '5', 'м'),
                               candidate('bgn', query, '9.77915', 'м', 'BGN')], query_unit='м')
    assert result.median_rate == Decimal('5')


@pytest.mark.parametrize('text', ['Подмяна на водосточна тръба', 'Смяна на водосточна тръба'])
def test_replacement_is_a_distinct_primary_process(text):
    assert parse_work(text).operations == {'replace'}
    assert canonical_work(text).operations == ('replace',)
    assert not retrieval_plan(text).matches('Ремонт на водосточна тръба')
    assert score_match(text, 'Ремонт на водосточна тръба').confidence == 0
    assert retrieval_plan(text).matches('Подмяна на водосточна тръба')


def test_replacement_is_bound_to_secondary_object_not_host():
    repair = canonical_work('Ремонт на водосточна тръба с подмяна на скоби')
    assert repair.object == 'downpipe'
    assert repair.operations == ('repair',)
    assert repair.secondary_work == (('replace', 'fastener'),)
    replacement = canonical_work('Подмяна на скоби за водосточна тръба')
    assert replacement.object == 'fastener'
    assert replacement.operations == ('replace',)
    assert not retrieval_plan('Подмяна на водосточна тръба').matches('Ремонт на водосточна тръба с подмяна на скоби')


@pytest.mark.parametrize('query', ['Ремонт на водосточна тръба', 'Поправка на водосточна тръба',
                                  'Подмяна на водосточна тръба', 'Смяна на водосточна тръба',
                                  'Ремонт на водосточна тръба на място'])
def test_literal_without_disassembly_is_a_hard_veto(query):
    quote = query + ' без демонтаж'
    assert not retrieval_plan(query).matches(quote)
    score = score_match(query, quote, prior=1000)
    assert score.confidence == 0
    assert score.reasons == ['explicit_without_disassembly_required']
    result = best_match(query, [candidate('restricted', quote)], query_unit='м')
    assert not result.is_confident
    assert result.pool_size == 0
    assert result.candidate is None and result.alternatives == []


def test_parent_context_cannot_authorize_literal_restriction():
    effective = 'Ремонт на водосточна тръба без демонтаж Ф25'
    assert not retrieval_plan(effective, raw_query='Ф25').matches(effective)
    assert score_match(effective, effective, raw_query='Ф25').confidence == 0
    assert retrieval_plan(effective).matches(effective)
    assert score_match(effective, effective, raw_query=effective).confidence == 1


@pytest.mark.parametrize(('query', 'quote'), [
    ('Монтаж на болт', 'Монтаж на скоба'), ('Монтаж на дюбел', 'Монтаж на болт'),
    ('Доставка и монтаж на врата самозатваряща се', 'Доставка и монтаж на летяща врата'),
    ('Разваляне на зид от газобетон', 'Разваляне на тухлен зид'),
])
def test_broad_family_is_not_a_complete_work_definition(query, quote):
    assert not best_match(query, [candidate('wrong', quote)], query_unit='м²').is_confident


def test_numeric_ids_are_stable_unique_and_not_raw_vocabulary():
    for section in catalog()['concept_ids'].values():
        assert len(set(section.values())) == len(section)
        assert all(isinstance(value, int) and value > 0 for value in section.values())
    plan = retrieval_plan(SHORT)
    assert plan.requirements() == {'object': 'paint', 'operations': ['install'], 'materials': ['latex']}
    assert plan.id_requirements() == {'object': 1041, 'operations': [102], 'materials': [216]}
    assert 'materials' not in retrieval_plan('Разваляне на зид').id_requirements()
    assert work_metadata(SHORT)['schema_version'] == 2


def test_json_corpus_enrichment_preserves_the_entire_price_evidence(tmp_path):
    from scripts.enrich_bg_corpus import enrich

    records = [{'id': 'SMR-1', 'desc': SHORT, 'unit': 'M2', 'money': {'amount': 5, 'currency': 'EUR'},
                'origin': {'kind': 'reference_web', 'ref': 'original'}, 'vatStatus': 'unknown'}]
    source, target = tmp_path / 'seed.json', tmp_path / 'derived.jsonl.gz'
    source.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    assert enrich(source, target)['records'] == 1
    with gzip.open(target, 'rt', encoding='utf-8') as stream:
        derived = json.loads(stream.readline())
    assert derived['quotation'] == records[0]
    assert derived['canonical_work']['work']['concept_ids']['object'] == 1041
    assert json.loads(source.read_text(encoding='utf-8')) == records


def test_extra_quoted_brand_is_optional_but_preserved_and_can_be_required():
    quote = LONG.replace('Леко Интерин', 'Друг продукт').replace('Оргахим', 'Друга фирма')
    work = canonical_work(quote)
    assert work.attributes['product'] == ('друг продукт',)
    assert work.attributes['manufacturer'] == ('друга фирма',)
    assert best_match(ROOM, [candidate('other', quote)], query_unit='м²').is_confident
    assert not best_match(LONG, [candidate('other', quote)], query_unit='м²').is_confident


def test_reference_and_approved_prices_both_contribute_evidence_to_median():
    result = best_match(SHORT, [candidate('operator', SHORT, '5', auto_pricing_eligible=True),
                                candidate('reference', LONG, '6.15', auto_pricing_eligible=False)], query_unit='м²')
    assert result.pool_refs == {'operator', 'reference'}
    assert result.median_rate == Decimal('5.575')
