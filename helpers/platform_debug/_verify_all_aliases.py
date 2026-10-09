# Verify: every patched corpus row -> active, bill_terms persisted, each term
# inside search_text AND matching the production boundary predicate,
# spot-check critical abbreviations through find_candidates.
import os, sys, json, asyncio, re
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text
from app.database import async_session_factory
from app.modules.cost_match.repository import CostBaseRepository, _MIN_TERM_LENGTH

BOUNDARY = "(^|[^а-яёa-z0-9_]){t}([^а-яёa-z0-9_]|$)"


async def main():
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "select id, code, description, unit, rate, is_active, metadata "
            "from oe_costs_item where metadata ? 'bill_terms' order by code"
        ))).fetchall()
        print(f"rows carrying bill_terms: {len(rows)}")

        fail, checked = [], 0
        inactive = [r[1] for r in rows if not r[5]]
        for r in rows:
            md = r[6] or {}
            terms = [t.strip().lower() for t in (md.get("bill_terms") or []) if t and t.strip()]
            st = (md.get("search_text") or "").lower()
            probs = []
            if not r[5]:
                probs.append("INACTIVE")
            if not terms:
                probs.append("empty bill_terms")
            for t in terms:
                checked += 1
                if t not in st:
                    probs.append(f"'{t}' not in search_text")
                    continue
                # production predicates: short terms use boundary regex,
                # long terms use substring on search_text
                if len(t) < _MIN_TERM_LENGTH:
                    rx = BOUNDARY.format(t=re.escape(t))
                    if not re.search(rx, st):
                        probs.append(f"short '{t}' fails boundary predicate")
            if probs:
                fail.append((r[1], str(r[2])[:50], probs))

        print(f"alias terms checked: {checked}")
        print(f"verified rows: {len(rows)-len(fail)}/{len(rows)}")
        print(f"inactive patched rows: {len(inactive)} {inactive[:15]}")
        if fail:
            print("\n--- FAILURES ---")
            for code, desc, probs in fail:
                print(f"  {code} {desc}")
                for p in probs:
                    print(f"      {p}")

        # spot-check: critical abbreviations through the real retrieval path
        print("\n--- live retrieval spot-checks ---")
        repo = CostBaseRepository(s)
        for term in ("ск", "ски", "тск", "пк", "пс", "ф2", "подов сифон",
                     "спирателен кран", "пожарен кран", "укрепваща конструкция",
                     "шлайфана", "пердашена", "гранит", "водомер"):
            cands = await repo.find_candidates(term, limit=40)
            patched = [c for c in cands
                       if (c.metadata_ or {}).get("bill_terms")] if cands else []
            codes = [c.code for c in cands[:8]]
            print(f"  '{term}' -> {len(cands)} cands, patched in pool: "
                  f"{[c.code for c in patched][:6]}, top: {codes}")

        # foreign-language guard among patched rows
        cjk = [r[1] for r in rows if re.search(r"[一-鿿]", r[2] or "")]
        print(f"\nCJK descriptions among patched: {len(cjk)} {cjk[:5]}")


asyncio.run(main())
