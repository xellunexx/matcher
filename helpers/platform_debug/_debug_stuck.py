import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory
from app.modules.cost_match.repository import retrieval_terms, CostBaseRepository
from app.modules.cost_match.matcher import canonical_tokens, best_match
from app.modules.cost_match.service import _to_candidate

LINES = [
    ("L414", "СК ф2``", "бр."),
    ("L417", "TСК ф2``", "бр."),
    ("L430", "ПС 17/17", "бр."),
    ("L392", "СК ф1``-с ел. задвижване", "бр."),
    ("L393", "СКИ ф1``", "бр."),
]


async def main():
    async with async_session_factory() as s:
        repo = CostBaseRepository(s)
        for tag, desc, unit in LINES:
            terms = retrieval_terms(desc)
            print(f"\n=== {tag} {desc!r} terms={terms} qtokens={canonical_tokens(desc)}")
            rows = await repo.find_candidates(desc, unit=unit, limit=40)
            print(f"    pool={len(rows)}")
            for r in rows[:14]:
                meta = r.metadata_ or {}
                bt = meta.get("bill_terms")
                print(f"    {r.code[:40]:42} {str(r.unit)[:6]:7} bt={str(bt)[:70]}")
            cands = [_to_candidate(r, "bg") for r in rows]
            res = best_match(desc, cands)
            if res is not None:
                cand = getattr(res, "candidate", None) or getattr(res, "item", None)
                code = getattr(res, "code", None) or getattr(res, "cost_item_code", None)
                print(f"    WIN {res}")


asyncio.run(main())
