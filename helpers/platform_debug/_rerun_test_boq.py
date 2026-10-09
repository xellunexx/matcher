import sys, io, os, asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + r"\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
os.environ.setdefault("DATABASE_SYNC_URL", "postgresql+psycopg2://postgres@127.0.0.1:56729/postgres")

import uuid
from app.database import async_session_factory
from app.modules.cost_match.service import CostMatchService
from app.modules.cost_match.schemas import BoqMatchRunCreate

BOQ_ID = uuid.UUID("3824c126-e054-4b65-aa51-16ddc493d821")

async def main():
    async with async_session_factory() as session:
        svc = CostMatchService(session)
        res = await svc.run_boq_match(boq_id=BOQ_ID, data=BoqMatchRunCreate(), created_by=None)
        await session.commit()
        print("lines:", res.lines, "priced:", res.positions_priced, "unpriced:", res.positions_unpriced)
        print("counts:", res.counts)

asyncio.run(main())
