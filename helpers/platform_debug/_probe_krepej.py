import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "select code, unit, rate, substr(description,1,85), is_active, source "
            "from oe_costs_item where lower(description) ~ '(крепеж|крепящ|метални конструк|стоманена конструк|укреп|конструкция)' "
            "order by is_active desc, code limit 40"))).fetchall()
        for r in rows:
            print(("ON " if r[4] else "off"), r[0][:40], r[1], r[2], r[5][:12], r[3])


asyncio.run(main())
