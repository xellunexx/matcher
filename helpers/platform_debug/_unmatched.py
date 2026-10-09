import asyncio, re
from collections import Counter
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text


async def main():
    eng = create_async_engine('postgresql+asyncpg://postgres:postgres@127.0.0.1:56729/postgres')
    SM = async_sessionmaker(eng)
    async with SM() as s:
        ids = [r[0] for r in (await s.execute(
            text("SELECT id FROM oe_cost_match_run WHERE name LIKE 'KSS Hisarya%' ORDER BY created_at DESC LIMIT 3"))).all()]
        rows = (await s.execute(text(
            "SELECT source_description, suggested_description, confidence FROM oe_cost_match_result "
            "WHERE tier='unmatched' AND run_id=ANY(:ids) ORDER BY confidence DESC"),
            {'ids': ids})).all()
        print('unmatched:', len(rows))
        # cluster by first content word
        heads = Counter()
        for src, sug, c in rows:
            w = re.findall(r'[А-Яа-яA-Za-z]{4,}', src or '')
            heads[w[0].lower() if w else '?'] += 1
        print('--- head-word clusters ---')
        for w, n in heads.most_common(30):
            print(f'{n:4} {w}')
        print('--- top-25 closest ---')
        for src, sug, c in rows[:25]:
            print(f'{float(c):.2f} {str(src)[:65]} => {str(sug)[:60]}')

asyncio.run(main())
