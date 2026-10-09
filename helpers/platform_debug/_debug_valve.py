import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from types import SimpleNamespace
from sqlalchemy import text
from app.database import async_session_factory
from app.modules.cost_match.service import CostMatchService
from app.modules.cost_match.matcher import canonical_tokens, score_match

FAKE_RUN = SimpleNamespace(
    id="replay", project_id="x", source_locale="bg", cost_source="all",
    region=None, catalog_id=None, candidate_limit=200)


async def main():
    from app.modules.cost_match.repository import CostBaseRepository
    async with async_session_factory() as s:
        repo = CostBaseRepository(s)
        for q in ["СК ф1/2``", "СК ф2``", "ПС 17/17", "ОК ф1/2``"]:
            items = await repo.find_candidates(
                q, cost_source="all", unit="бр", limit=200)
            codes = [i.code for i in items]
            hit = [i for i in items if i.code == "BUILDLY-SMR-БОЯ-0041"]
            print(f"\n== {q}  pool={len(items)} БОЯ-0041 in pool: {bool(hit)}")
            print("   q tokens:", canonical_tokens(q))
            if hit:
                i = hit[0]
                meta = i.metadata_ or {}
                bt = meta.get("bill_terms")
                print("   bill_terms:", bt)
                scored = f"{i.description} {' '.join(bt)}"
                sc = score_match(q, scored, query_unit="бр", candidate_unit=i.unit)
                print("   score:", round(sc.confidence, 4), sc.reasons,
                      {k: round(v, 3) for k, v in sc.factors.items()})
            # what did the full pipeline say
            svc = CostMatchService(s)
            res = await svc._score_line(description=q, unit="бр", source_ref=None, run=FAKE_RUN)
            print("   pipeline:", res["tier"], res["confidence"], res["suggested_code"],
                  "|", res["suggested_description"][:60])
            for a in res["alternatives"][:4]:
                print("      alt:", a["code"], a["confidence"], a["description"][:60])


asyncio.run(main())
