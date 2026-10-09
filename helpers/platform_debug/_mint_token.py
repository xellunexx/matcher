import sys, io, asyncio, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:53642/postgres")
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def m():
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        tables = [r[0] for r in (await c.execute(text(
            "select table_name from information_schema.tables where table_name like '%user%' or table_name like '%account%'"
        )))]
        print("tables:", tables)
        for t in tables:
            try:
                cols = [r[0] for r in (await c.execute(text(
                    f"select column_name from information_schema.columns where table_name='{t}'"
                )))]
                print(t, "cols:", cols[:15])
            except Exception as ex:
                print(t, "err", ex)
    await e.dispose()

asyncio.run(m())
