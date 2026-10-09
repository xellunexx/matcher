import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text


async def main():
    eng = create_async_engine('postgresql+asyncpg://postgres:postgres@127.0.0.1:56729/postgres')
    SM = async_sessionmaker(eng)
    async with SM() as s:
        print('--- "Разрушаване на сгради" in corpus ---')
        rows = (await s.execute(text(
            "SELECT source, is_active, code, description, unit, rate, region FROM oe_costs_item WHERE description ILIKE :p"),
            {'p': '%Разрушаване на сгради%'})).all()
        for r in rows:
            print(' ', r[0], 'active=' + str(r[1]), '|', str(r[2])[:30], '|', str(r[3])[:60], '|', r[4], r[5], r[6])
        print('--- manual_entry items ---')
        rows = (await s.execute(text(
            "SELECT is_active, description, unit, rate, region FROM oe_costs_item WHERE source='manual_entry' LIMIT 25"))).all()
        for r in rows:
            print('  active=' + str(r[0]), '|', str(r[1])[:60], '|', r[2], r[3], r[4])

asyncio.run(main())
