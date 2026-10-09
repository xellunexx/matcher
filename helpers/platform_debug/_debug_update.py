import os, sys, asyncio, json
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

        r = (await s.execute(text(
            "select code, metadata from oe_costs_item "
            "where code='BUILDLY-SMR-ДОВ-0088'"))).fetchone()
        print("before:", str(r[1])[:300])
        meta = dict(r[1] or {})
        meta["bill_terms"] = ["тесттерм"]
        res = await s.execute(text(
            "update oe_costs_item set metadata = cast(:m as jsonb) "
            "where code='BUILDLY-SMR-ДОВ-0088'"), {"m": json.dumps(meta, ensure_ascii=False)})
        print("update rowcount:", res.rowcount)
        await s.commit()

    async with async_session_factory() as s2:
        r = (await s2.execute(text(
            "select metadata->'bill_terms' from oe_costs_item "
            "where code='BUILDLY-SMR-ДОВ-0088'"))).fetchone()
        print("after:", r[0])


asyncio.run(main())
