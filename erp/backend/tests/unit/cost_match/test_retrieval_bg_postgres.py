"""Real PostgreSQL execution of the ERP repository's signature retrieval."""

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest


def test_repository_filters_current_signatures_before_limits(monkeypatch) -> None:
    pg = pytest.importorskip('pixeltable_pgserver')
    pytest.importorskip('asyncpg')
    from sqlalchemy import create_engine, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    temporary = Path.cwd() / f'bg-pg-test-{uuid.uuid4().hex}'
    temporary.mkdir()
    pgdata = temporary / 'postgres'
    server = pg.get_server(pgdata, cleanup_mode='stop')
    monkeypatch.setenv('DATABASE_URL', server.get_uri(driver='asyncpg'))
    from app.modules.cost_match import repository as repository_module
    from app.modules.cost_match.matcher import Candidate, best_match
    from app.modules.cost_match.repository import CostBaseRepository, CostItem

    async def forbidden_vector_call(*args, **kwargs):
        raise AssertionError('Bulgarian signature retrieval must not bypass its pool with vector hits')

    monkeypatch.setattr(repository_module, '_vector_candidate_ids', forbidden_vector_call)

    sync_engine = create_engine(server.get_uri(driver='psycopg2'))
    CostItem.__table__.create(sync_engine)

    async def run() -> None:
        engine = create_async_engine(server.get_uri(driver='asyncpg'))
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                repository = CostBaseRepository(session)
                query = 'Разваляне на 25 см. тухлен зид'

                def item(code, description, rate='30', **kwargs):
                    return CostItem(code=code, description=description, unit='м²', rate=rate,
                                    currency='EUR', source=kwargs.pop('source', 'operator_pricelist'), is_active=True,
                                    metadata_={'keep': {'source': 'original'}}, **kwargs)

                rows = [item(f'A{i:03}', 'Разваляне на кофраж') for i in range(70)]
                rows[0].metadata_ = ['legacy operator reference', {'ref': 'original'}]
                rows += [
                    item('HOST', 'Премахване на мазилка от тухлен зид'),
                    item('BUILD', 'Направа на тухлена зидария'),
                    item('WALL', query, '35', region='BG_SOFIA'),
                    item('OTHER_PRICE', query, '95'),
                    item('SYNONYM', 'Отстраняване на 25 см тухлена стена'),
                    item('SPEC', 'Разваляне на 12 см тухлен зид'),
                    item('STONE', 'Разваляне на зид от газобетон'),
                    item('OUTSIDE', query, region='US'),
                    item('LOCALIZED', 'Original untranslated source', descriptions={'bg': '\xa0\t' + query + ' \n'}),
                    item('SPACED', '\xa0\t' + query + ' \n'),
                    item('UNKNOWN', 'Доставка и монтаж на чела стълби 5 бр.x1.75м. Гранит'),
                    item('FRAGMENT', 'Ф25'),
                ]
                rows[-1].metadata_['work_context'] = ['Доставка и монтаж на тръба PPR PN20']
                catalog_id = uuid.uuid4()
                rows += [item('CATALOG_ONLY', query, source='test_catalog_external', catalog_id=catalog_id),
                         item('CATALOG_OTHER', query, source='test_catalog_external', catalog_id=uuid.uuid4()),
                         item('INACTIVE', query)]
                rows[-1].is_active = False
                session.add_all(rows)
                await session.flush()
                original = {row.code: (row.description, row.rate, row.unit, row.currency, row.source)
                            for row in rows}

                result = await repository.find_candidates(query, cost_source='operator', region='BG',
                                                          unit='м²', limit=1)
                codes = {row.code for row in result}
                assert codes == {'WALL', 'OTHER_PRICE', 'SYNONYM', 'SPEC', 'LOCALIZED', 'SPACED'}
                assert len(result) > 1
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 0
                assert 'canonical_work' not in rows[-1].metadata_
                assert 'canonical_work' not in next(row for row in rows if row.code == 'OUTSIDE').metadata_
                for row in rows:
                    assert original[row.code] == (row.description, row.rate, row.unit, row.currency, row.source)
                    if row.code == 'A000':
                        assert row.metadata_['legacy_metadata'] == ['legacy operator reference', {'ref': 'original'}]
                    else:
                        assert row.metadata_['keep'] == {'source': 'original'}
                pool = [Candidate(row.code, query if row.code == 'LOCALIZED' else row.description,
                                  row.unit, {'unit_rate': row.rate, 'currency': row.currency})
                        for row in result]
                assert not best_match(query, pool, query_unit='м²', locale='bg').is_confident

                building = await repository.find_candidates('Изграждане на тухлен зид',
                                                             cost_source='operator', region='BG', limit=1)
                assert {row.code for row in building} == {'BUILD'}
                no_material = await repository.find_candidates('Разваляне на зид',
                                                                cost_source='operator', region='BG', limit=1)
                assert 'STONE' in {row.code for row in no_material}

                wall = next(row for row in rows if row.code == 'WALL')
                wall.description = 'Премахване на мазилка от тухлен зид'
                await session.flush()
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 1
                assert wall.metadata_['canonical_work']['work']['object'] == 'plaster'

                localized = next(row for row in rows if row.code == 'LOCALIZED')
                localized.descriptions = {'bg': 'Премахване на мазилка от тухлен зид'}
                fragment = next(row for row in rows if row.code == 'FRAGMENT')
                fragment.metadata_ = {**fragment.metadata_,
                                      'work_context': ['Доставка и монтаж на компенсатор Ф25']}
                await session.flush()
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 2
                assert localized.metadata_['canonical_work']['work']['object'] == 'plaster'
                assert fragment.metadata_['canonical_work']['work']['object'] == 'compensator'

                synonym = next(row for row in rows if row.code == 'SYNONYM')
                synonym.metadata_ = {**synonym.metadata_, 'canonical_work':
                                     {**synonym.metadata_['canonical_work'], 'retrieval_index_version': 0}}
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 1
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 0
                signature = {**synonym.metadata_['canonical_work'], 'catalog_digest': 'obsolete'}
                synonym.metadata_ = {**synonym.metadata_, 'canonical_work': signature}
                await session.flush()
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 1
                assert await repository.refresh_bg_signatures(cost_source='operator', region='BG') == 0
                result = await repository.find_candidates(query, cost_source='operator', region='BG', limit=1)
                assert {row.code for row in result} == {'OTHER_PRICE', 'SYNONYM', 'SPEC', 'SPACED'}
                unknown = await repository.find_candidates(
                    'Доставка и монтаж на чела стълби 5 бр.x1.75м. Гранит',
                    cost_source='operator', region='BG', limit=1)
                assert {row.code for row in unknown} == {'UNKNOWN'}
                scoped = await repository.find_candidates(query, cost_source='test_catalog_external', region='BG',
                                                          catalog_id=catalog_id, limit=1)
                assert {row.code for row in scoped} == {'CATALOG_ONLY'}
                assert len(list((await session.scalars(select(CostItem))).all())) == len(rows)
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    finally:
        sync_engine.dispose()
        server.cleanup()
        shutil.rmtree(temporary)
