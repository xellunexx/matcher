import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = (await s.execute(text(
            "select tier, count(*), min(line_no), max(line_no) "
            "from oe_cost_match_result where run_id=:r group by tier"),
            {"r": "c6bc0407-f256-402b-99a6-a38ca8d3ce65"})).fetchall()
        print("run c6bc0407 tiers:", r)
        r2 = (await s.execute(text(
            "select id, created_at, status from oe_cost_match_run "
            "order by created_at desc limit 5"))).fetchall()
        for x in r2:
            print(x[0], x[1], x[2])


asyncio.run(main())
