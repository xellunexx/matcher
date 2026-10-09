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
        for r in (await c.execute(text(
            "select coalesce(source,'<null>') s, count(*) n, count(rate) priced "
            "from oe_costs_item where is_active group by 1 order by n desc"
        ))):
            print(f"{r.n:7}  priced={r.priced:6}  {r.s}")
        print()
        # how many rows have code prefixes matching seed files
        for r in (await c.execute(text(
            "select substring(code from '^[A-Z]+[-_]') pref, count(*) n "
            "from oe_costs_item where is_active group by 1 order by n desc limit 15"
        ))):
            print(f"{r.n:7}  {r.pref}")
    await eng.dispose()

asyncio.run(main())
