import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = (await s.execute(text(
            "select column_name from information_schema.columns "
            "where table_name='oe_costs_item' order by ordinal_position"))).fetchall()
        print("columns:", [x[0] for x in r])
        r2 = (await s.execute(text(
            "select code, coalesce(metadata->>'search_text','<none>') st, "
            "coalesce(metadata->>'bill_terms','<none>') bt "
            "from oe_costs_item where code like '%МАТ-434' limit 2"))).fetchall()
        for x in r2:
            print(x)


asyncio.run(main())
