import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.modules.cost_match.service import CostMatchService

BOQ = "5726cd49-1430-41ff-8ff7-a69886e0f3c0"

async def main():
    import os
    os.environ.setdefault("OE_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:56729/postgres")
    eng = create_async_engine("postgresql+psycopg://postgres@127.0.0.1:56729/postgres")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        svc = CostMatchService(s)
        summary = await svc.run_boq_match(BOQ, cost_source="all")
        await s.commit()
        print("SUMMARY:", summary)

asyncio.run(main())
