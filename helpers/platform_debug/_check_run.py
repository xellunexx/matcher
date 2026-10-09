import sys, io, asyncio, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:53642/postgres"
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as c:
        cols = [r[0] for r in (await c.execute(text(
            "select column_name from information_schema.columns where table_name='oe_cost_match_run'"
        )))]
        print("run cols:", cols)
        print("\n-- last 5 runs --")
        selcols = ", ".join(x for x in ["id","boq_id","status","counts","created_at","updated_at","finished_at","positions_priced","positions_unpriced","lines"] if x in cols)
        for r in (await c.execute(text(
            f"select {selcols} from oe_cost_match_run order by created_at desc limit 5"
        ))):
            print(dict(r._mapping))
        print("\n-- positions BOQ 9038d626 --")
        for r in (await c.execute(text(
            "select coalesce(price_basis,'<none>') b, count(*) n "
            "from oe_boq_position where boq_id='9038d626-9261-47bc-bc82-e42a6ff2847f' "
            "group by 1 order by n desc"
        ))):
            print(f"{r.n:6}  {r.b}")
        pr = (await c.execute(text(
            "select count(*) from oe_boq_position where boq_id='9038d626-9261-47bc-bc82-e42a6ff2847f'"
        ))).scalar()
        print("total positions:", pr)
        # is cwicr still deactivated after reboot?
        cw = (await c.execute(text(
            "select count(*) from oe_costs_item where is_active"
        ))).scalar()
        print("active corpus rows:", cw)
    await eng.dispose()

asyncio.run(main())
