import sys, io, asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def m():
    e = create_async_engine('postgresql+asyncpg://postgres@127.0.0.1:53642/postgres')
    async with e.connect() as c:
        rows = (await c.execute(text(
            "select coalesce(price_basis,'<none>') b, count(*) n "
            "from oe_boq_position where boq_id='9038d626-9261-47bc-bc82-e42a6ff2847f' "
            "group by 1 order by 2 desc"
        ))).all()
        print({r.b: r.n for r in rows})
        runs = (await c.execute(text(
            "select id, status, created_at from oe_cost_match_run order by created_at desc limit 2"
        ))).all()
        for r in runs:
            print(dict(r._mapping))
    await e.dispose()

asyncio.run(m())
