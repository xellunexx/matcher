import sys, io, os, asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + r"\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"

from app.database import async_session_factory
from app.modules.cost_match.repository import CostBaseRepository, retrieval_terms

DESC = "Разрушаване на сгради и басейни с багер-чук - бетонови плочи, греди, стени, колони, основи"

async def main():
    print("retrieval_terms:", retrieval_terms(DESC))
    async with async_session_factory() as s:
        repo = CostBaseRepository(s)
        cands = await repo.find_candidates(DESC, limit=200)
        print("candidates:", len(cands))
        for c in cands[:15]:
            print("  ", c.unit, c.rate, c.currency, c.source, "|", (c.description or "")[:75])
        hits = [c for c in cands if "БАГЕР" in (c.description or "").upper() and c.unit in ("M3", "м3")]
        print("M3 багер hits:", len(hits))
        for c in hits:
            print("  HIT", c.unit, c.rate, c.currency, "|", (c.description or "")[:75])

asyncio.run(main())
