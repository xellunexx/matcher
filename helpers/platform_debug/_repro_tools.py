import os
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
os.environ["DATABASE_SYNC_URL"] = "postgresql+psycopg2://postgres@127.0.0.1:56729/postgres"
import asyncio, sys, traceback
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")


async def main():
    from app.database import async_session_factory as session_factory
    from app.modules.erp_chat import tools

    async with session_factory() as session:
        from sqlalchemy import text
        uid = (await session.execute(text("SELECT id FROM oe_users_user LIMIT 1"))).scalar()
        pid = (await session.execute(text("SELECT project_id FROM oe_boq_boq WHERE id='57df5cfb-bb38-4fdf-9ca5-af355cf1c7c6'"))).scalar()
        print("user:", uid, "project:", pid)

        cases = (
            ("get_boq_items", {"project_id": str(pid)}),
            ("get_boq_items", {"project_id": str(pid), "boq_id": "57df5cfb-bb38-4fdf-9ca5-af355cf1c7c6"}),
            ("search_cwicr_database", {"query": "бетон"}),
            ("search_cwicr_database", {"search_query": "зидария", "region": "BG"}),
            ("get_validation_results", {"project_id": str(pid)}),
            ("run_validation", {"project_id": str(pid)}),
            ("get_cost_model", {"project_id": str(pid)}),
        )
        for name, args in cases:
            handler = tools.TOOL_HANDLER_MAP[name]
            try:
                res = await handler(session, args, str(uid))
                print(f"\n=== {name} {args} ===")
                print("summary:", res.get("summary"))
                d = res.get("data") or {}
                for k in ("positions", "items", "reports"):
                    if k in d:
                        print(f"{k}:", len(d[k]))
            except Exception:
                print(f"\n=== {name} RAISED ===")
                traceback.print_exc()


asyncio.run(main())
