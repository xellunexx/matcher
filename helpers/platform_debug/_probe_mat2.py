import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        print("=== МАТ-420..470 + valve/siphon/filter rows, no rate filter ===")
        rows = (await s.execute(text(
            "select code, unit, rate, substr(description,1,88), is_active from oe_costs_item "
            "where (code ~ 'МАТ-4' or lower(description) ~ '(кран|клапа|сифон|филтър|обезвъзд|водомер|холендър|калот|шибър|вентил)') "
            "and lower(description) !~ '(под наем|верижни|автокран|мобилен кран|друг кран|вентилатор|карбон|hepa)' "
            "order by code limit 200"))).fetchall()
        for r in rows:
            act = "ON" if r[4] else "OFF"
            print(f"  {act} {r[0][:38]:40} {str(r[1])[:6]:7} {str(r[2])[:9]:10} {r[3]}")
        print("total:", len(rows))


asyncio.run(main())
