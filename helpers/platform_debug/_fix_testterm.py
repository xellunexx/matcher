import os, sys, asyncio, json
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text
from app.database import async_session_factory
from app.modules.cost_match.repository import CostBaseRepository

async def m():
    async with async_session_factory() as s:
        r = (await s.execute(text(
            "select metadata from oe_costs_item where code='BUILDLY-SMR-ДОВ-0088'"))).fetchone()
        md = r[0]
        print("before:", md.get("bill_terms"))
        md["bill_terms"] = [t for t in md.get("bill_terms", []) if t != "тесттерм"]
        await s.execute(text(
            "update oe_costs_item set metadata=:m where code='BUILDLY-SMR-ДОВ-0088'"),
            {"m": json.dumps(md, ensure_ascii=False)})
        await s.commit()
        repo = CostBaseRepository(s)
        for t in ("СК", "ПК", "ПС", "ТСК", "ОК", "СКИ"):
            cands = await repo.find_candidates(t, limit=40)
            print(f"'{t}' -> {len(cands)} cands:",
                  [c.code.split('-')[-1] for c in cands[:10]])

asyncio.run(m())
