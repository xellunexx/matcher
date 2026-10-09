"""Rebuild derived Bulgarian signatures without changing quotations or rates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONCEPT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_cost_items_bg_meaning_v2
ON oe_costs_item USING gin
((CAST(metadata -> 'canonical_work' -> 'work' -> 'concept_ids' AS jsonb)))
"""


async def backfill(url: str, *, source: str | None, region: str | None,
                   commit: bool, ensure_index: bool) -> dict[str, int | bool]:
    from app.modules.cost_match.repository import CostBaseRepository

    connection_url = make_url(url).set(drivername='postgresql+asyncpg')
    engine = create_async_engine(connection_url)
    try:
        async with AsyncSession(engine) as session:
            if ensure_index:
                await session.execute(text(CONCEPT_INDEX_SQL))
            rebuilt = await CostBaseRepository(session).refresh_bg_signatures(cost_source=source, region=region)
            if commit:
                await session.commit()
            else:
                await session.rollback()
            return {'signatures_rebuilt': rebuilt, 'committed': commit,
                    'concept_index_installed': ensure_index and commit}
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=None, help='Optional existing ERP cost-source scope, e.g. operator')
    parser.add_argument('--region', default=None)
    parser.add_argument('--commit', action='store_true', help='Persist the derived metadata (otherwise dry run)')
    parser.add_argument('--ensure-index', action='store_true', help='Install the PostgreSQL GIN concept index')
    args = parser.parse_args()
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        parser.error('DATABASE_URL must be set to the ERP database; do not put credentials in command arguments.')
    if make_url(database_url).get_backend_name() != 'postgresql':
        parser.error('This signature backfill requires PostgreSQL.')
    print(json.dumps(asyncio.run(backfill(database_url, source=args.source, region=args.region,
                                         commit=args.commit, ensure_index=args.ensure_index))))
