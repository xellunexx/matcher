import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "select code, metadata->>'search_text' st, metadata->>'bill_terms' bt "
            "from oe_costs_item where code ~ '(МАТ-43[0-9]|МАТ-44[0-1]|МАТ-42[4-7]|СУХ-014[0-9]|СУХ-0150|СМР-0024|ОБЩ-1197)' "
            "and is_active order by code"))).fetchall()
        for code, st, bt in rows:
            print(f"{code[:42]:44} bt={str(bt)[:80]}")
        # direct regex probes
        for pat in [r"\yск\y", r"\yф\s*2\y", r"\yтск\y", r"\yпс\y"]:
            n = (await s.execute(text(
                "select count(*) from oe_costs_item where is_active and "
                "(lower(description) ~* :p or lower(coalesce(metadata->>'search_text','')) ~* :p)"),
                {"p": pat})).scalar()
            print(f"regex {pat!r}: {n} active rows")


asyncio.run(main())
