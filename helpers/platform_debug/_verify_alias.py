import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text, select
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = (await s.execute(text(
            "select code, metadata->'bill_terms' as bt, metadata->>'search_text' as st "
            "from oe_costs_item where code='BUILDLY-SMR-ДОВ-0088'"))).fetchone()
        print("DB bill_terms:", r[1])
        print("DB search_text tail:", (r[2] or "")[-160:])

        # does the ORM object expose it?
        from app.modules.cost_match.models import CostItem
        item = (await s.execute(
            select(CostItem).where(CostItem.code == "BUILDLY-SMR-ДОВ-0088"))).scalar_one()
        print("ORM metadata_ bill_terms:", (item.metadata_ or {}).get("bill_terms"))

        from app.modules.cost_match.service import _to_candidate
        cand = _to_candidate(item, "bg")
        print("candidate.text:", cand.text)

        from app.modules.cost_match.matcher import score_match, canonical_tokens
        sc = score_match("Водомер (с дистанционно отчитане)", cand.text, query_unit="бр", candidate_unit=cand.unit)
        print("score:", sc.confidence, sc.reasons, {k: round(v,3) for k,v in sc.factors.items()})
        print("q tokens:", canonical_tokens("Водомер (с дистанционно отчитане)"))


asyncio.run(main())
