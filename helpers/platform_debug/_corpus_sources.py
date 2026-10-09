import sys, io, asyncio, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ["OE_DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    eng = create_async_engine(os.environ["OE_DATABASE_URL"])
    async with eng.connect() as c:
        print("-- by source --")
        for r in (await c.execute(text(
            "select source, count(*) n from oe_costs_item where is_active group by source order by n desc"
        ))):
            print(f"{r.n:7}  {r.source}")
        print("-- by classification --")
        for r in (await c.execute(text(
            "select classification, count(*) n from oe_costs_item where is_active group by classification order by n desc"
        ))):
            print(f"{r.n:7}  {r.classification}")
        print("-- sample of biggest non-seed source --")
        for r in (await c.execute(text(
            "select code, left(description,70) d, unit, rate, source from oe_costs_item "
            "where is_active order by random() limit 12"
        ))):
            print(f"{str(r.source):20} {str(r.rate):>12} {str(r.unit):14} {str(r.code)[:22]:22} {r.d}")
    await eng.dispose()

asyncio.run(main())
