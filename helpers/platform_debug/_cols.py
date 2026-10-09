import sys, io, asyncio, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as c:
        for r in (await c.execute(text(
            "select column_name, data_type from information_schema.columns "
            "where table_name='oe_cost_match_result' order by ordinal_position"
        ))):
            print(r.column_name, r.data_type)
    await eng.dispose()

asyncio.run(main())
