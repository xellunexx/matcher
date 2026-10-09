import sys, io, asyncio, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ["OE_DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    eng = create_async_engine(os.environ["OE_DATABASE_URL"])
    async with eng.begin() as c:
        r = await c.execute(text(
            "update oe_costs_item set is_active=false where source='cwicr' and is_active"
        ))
        print("deactivated:", r.rowcount)
        n = (await c.execute(text(
            "select count(*) from oe_costs_item where is_active"
        ))).scalar()
        print("active now:", n)
        for r in (await c.execute(text(
            "select coalesce(source,'<null>') s, count(*) n "
            "from oe_costs_item where is_active group by 1 order by n desc"
        ))):
            print(f"{r.n:7}  {r.s}")
    await eng.dispose()

asyncio.run(main())
