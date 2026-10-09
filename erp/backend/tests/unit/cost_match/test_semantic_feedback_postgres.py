"""A saved review must affect the next match, not just its audit note."""

import asyncio
import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_saved_semantic_rulings_replay_in_postgres_without_bypassing_eligibility(monkeypatch):
    pg = pytest.importorskip('pixeltable_pgserver')
    pytest.importorskip('asyncpg')
    from sqlalchemy import create_engine, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    temporary = Path.cwd() / f'feedback-pg-test-{uuid.uuid4().hex}'
    temporary.mkdir()
    server = pg.get_server(temporary / 'postgres', cleanup_mode='stop')
    monkeypatch.setenv('DATABASE_URL', server.get_uri(driver='asyncpg'))
    from app.modules.cost_match import semantic_feedback
    from app.modules.cost_match import service as service_module
    from app.modules.cost_match.matcher import best_match
    from app.modules.cost_match.models import MatchDecision, MatchPattern, MatchResult, MatchRun
    from app.modules.cost_match.repository import CostItem
    from app.modules.cost_match.schemas import MatchDecisionCreate
    from app.modules.cost_match.semantic_feedback import decode_link, semantic_links
    from app.modules.cost_match.service import CostMatchService, _to_candidate
    from app.modules.projects.models import Project, ProjectMilestone, ProjectWBS
    from app.modules.users.models import User

    monkeypatch.setattr(service_module.event_bus, 'publish_detached', lambda *args, **kwargs: None)
    sync_engine = create_engine(server.get_uri(driver='psycopg2'))
    for model in (User, Project, ProjectWBS, ProjectMilestone, CostItem,
                  MatchRun, MatchResult, MatchDecision, MatchPattern):
        model.__table__.create(sync_engine)

    query = 'Демонтаж на осветителни тела'
    quotation = 'Демонтаж на съществуващи осветителни тела'
    tenant_id = uuid.uuid4()

    async def scenario():
        engine = create_async_engine(server.get_uri(driver='asyncpg'))
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                user = User(email='reviewer@example.test', hashed_password='test-only-not-authentication')
                session.add(user)
                await session.flush()
                project = Project(name='review fixture', owner_id=user.id, currency='EUR')
                session.add(project)
                await session.flush()
                run = MatchRun(project_id=project.id, source_locale='bg', cost_source='operator',
                               region='BG', tenant_id=tenant_id, candidate_limit=1)
                item = CostItem(code='LESSON', description=quotation, descriptions={}, unit='бр.',
                                rate='5', currency='EUR', source='operator_pricelist',
                                region='BG', is_active=True, metadata_={})
                session.add_all([run, item])
                await session.flush()
                result = MatchResult(run_id=run.id, project_id=project.id, source_description=query,
                                     source_unit='бр.', tier='needs_review', confidence=Decimal('0.7'),
                                     suggested_cost_item_id=item.id, suggested_code=item.code,
                                     suggested_description=quotation, suggested_unit=item.unit,
                                     suggested_rate=Decimal('5'), suggested_currency='EUR',
                                     factors={}, reason_codes=[], alternatives=[])
                session.add(result)
                await session.flush()
                service = CostMatchService(session)
                service._position_for_result = AsyncMock(return_value=None)
                link = semantic_links(query, quotation)[0]
                await service.record_decision(run, result, MatchDecisionCreate(
                    decision='confirmed', semantic_choices=[{'link_id': link['id'], 'verdict': 'same'}]),
                    decided_by=user.id, propagate_duplicates=False)
                await session.commit()
                run_id, result_id, item_id, reviewer = run.id, result.id, item.id, user.id

            async with AsyncSession(engine, expire_on_commit=False) as session:
                run = await session.get(MatchRun, run_id)
                result = await session.get(MatchResult, result_id)
                item = await session.get(CostItem, item_id)
                service = CostMatchService(session)
                service._position_for_result = AsyncMock(return_value=None)
                patterns = list((await session.scalars(select(MatchPattern))).all())
                assert len(patterns) == 1
                assert len(patterns[0].shared_tokens) == 1
                assert decode_link(patterns[0].shared_tokens[0])['verdict'] == 'same'
                assert patterns[0].candidate_text == quotation
                candidate = _to_candidate(item, 'bg')
                assert await service._pattern_priors(query, [candidate], run) == {str(item.id): 1.05}
                assert candidate.payload['human_semantic_links'][0]['id'] == link['id']

                # The approved candidate returns even if signature recall missed it.
                original_retrieval = service.base_repo.find_candidates
                service.base_repo.find_candidates = AsyncMock(return_value=[])
                scored = await service._score_line(description=query, unit='бр.', source_ref='', run=run)
                assert scored['suggested_cost_item_id'] == item.id
                assert scored['factors']['human_semantic_links'][0]['id'] == link['id']
                result.factors = scored['factors']
                result.reason_codes = scored['reason_codes']
                response = service.result_response(result, locale='bg')
                assert response.factors['semantic_links'][0]['id'] == link['id']
                assert response.explanation
                service.base_repo.find_candidates = original_retrieval

                for field, value in [('cost_source', 'cwicr'), ('region', 'DE'),
                                     ('tenant_id', uuid.uuid4()), ('catalog_id', uuid.uuid4())]:
                    scope = SimpleNamespace(cost_source=run.cost_source, region=run.region,
                                            tenant_id=run.tenant_id, catalog_id=run.catalog_id)
                    setattr(scope, field, value)
                    assert await service._pattern_priors(query, [_to_candidate(item, 'bg')], scope) == {}

                assert await service._pattern_priors(query + ' с обратен монтаж', [candidate], run) == {}
                for field, value in [('description', quotation + ' с извозване'),
                                     ('code', 'OTHER'), ('unit', 'м²')]:
                    original = getattr(item, field)
                    setattr(item, field, value)
                    assert await service._pattern_priors(query, [_to_candidate(item, 'bg')], run) == {}
                    setattr(item, field, original)
                with monkeypatch.context() as context:
                    context.setattr(semantic_feedback, 'catalog_digest', lambda: 'revised-rule-catalog')
                    assert await service._pattern_priors(query, [_to_candidate(item, 'bg')], run) == {}
                    assert await service._remembered_candidates(query, run) == []

                # Positive semantic evidence is not source eligibility or VAT/currency permission.
                item.metadata_ = {'origin_kind': 'reference_web', 'tenderops_status': 'pending_review'}
                await session.flush()
                scored = await service._score_line(description=query, unit='бр.', source_ref='', run=run)
                assert scored['tier'] == 'needs_review'
                item.metadata_ = {}
                item.is_active = False
                await session.flush()
                assert await service._remembered_candidates(query, run) == []
                item.is_active = True
                await session.flush()

                await service.record_decision(run, result, MatchDecisionCreate(
                    decision='rejected', semantic_choices=[{'link_id': link['id'], 'verdict': 'different'}]),
                    decided_by=reviewer, propagate_duplicates=False)
                await session.commit()
                candidate = _to_candidate(item, 'bg')
                assert await service._pattern_priors(query, [candidate], run) == {}
                assert candidate.payload['human_semantic_rejection'] is True
                assert best_match(query, [candidate], query_unit='бр.').candidate is None
                assert await service._remembered_candidates(query, run) == []

                # Merely confirming a price does not retract a rejected semantic role.
                await service.record_decision(run, result, MatchDecisionCreate(decision='confirmed'),
                                              decided_by=reviewer, propagate_duplicates=False)
                candidate = _to_candidate(item, 'bg')
                assert await service._pattern_priors(query, [candidate], run) == {}
                assert candidate.payload['human_semantic_rejection'] is True
                await service.record_decision(run, result, MatchDecisionCreate(
                    decision='confirmed', semantic_choices=[{'link_id': link['id'], 'verdict': 'same'}]),
                    decided_by=reviewer, propagate_duplicates=False)
                candidate = _to_candidate(item, 'bg')
                assert await service._pattern_priors(query, [candidate], run) == {str(item.id): 1.05}
                assert not candidate.payload.get('human_semantic_rejection')
                assert len(list((await session.scalars(select(MatchPattern))).all())) == 3
                assert len(list((await session.scalars(select(MatchDecision))).all())) == 4
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        sync_engine.dispose()
        server.cleanup()
        shutil.rmtree(temporary)
