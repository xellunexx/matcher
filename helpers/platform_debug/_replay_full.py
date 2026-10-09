# Full-bill replay through the live ERP pipeline. Read-only.
import os, sys, asyncio, json
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")

from types import SimpleNamespace
from sqlalchemy import text
from app.database import async_session_factory
from app.modules.cost_match.service import CostMatchService

RUN_ID = "c6bc0407-f256-402b-99a6-a38ca8d3ce65"
FAKE_RUN = SimpleNamespace(
    id="replay", project_id="07996738-c379-4cd8-a84a-275fa0102d67",
    source_locale="bg", cost_source="all", region=None,
    catalog_id=None, candidate_limit=200,
)


async def main():
    async with async_session_factory() as session:
        svc = CostMatchService(session)
        rows = (await session.execute(text(
            "select line_no, source_description, source_unit, source_quantity "
            "from oe_cost_match_result where run_id=:r order by line_no"),
            {"r": RUN_ID})).fetchall()
        print(f"replaying {len(rows)} lines", flush=True)
        out = []
        seen = {}
        for i, (line_no, desc, unit, qty) in enumerate(rows):
            key = (desc or "", unit or "")
            if key not in seen:
                try:
                    seen[key] = await svc._score_line(
                        description=desc or "", unit=unit or "",
                        source_ref=None, run=FAKE_RUN)
                except Exception as exc:
                    seen[key] = {"tier": "error", "error": str(exc)[:200]}
            res = dict(seen[key])
            res["line_no"] = line_no
            res["desc"] = (desc or "")[:110]
            res["qty"] = str(qty) if qty is not None else None
            out.append(res)
            if (i + 1) % 60 == 0:
                print(f"  {i+1}/{len(rows)}", flush=True)
        with open(r"C:\lab\tenderops\platform\_replay_full.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
        from collections import Counter
        print(Counter(r["tier"] for r in out), flush=True)


asyncio.run(main())
