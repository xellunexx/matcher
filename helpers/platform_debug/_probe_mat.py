import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        # МАТ series = dense ВиК/general materials - look at everything plumbing-ish
        rows = (await s.execute(text(
            "select code, unit, rate, substr(description,1,90) from oe_costs_item "
            "where is_active and coalesce(rate,'')<>'' "
            "and code ~ '(МАТ|СУХ|ВИК|ОСВ)' "
            "and lower(description) ~ '(кран|клап|сифон|филтър|водомер|обезвъзд|холендър|маномет|термомет|разширител|калот|шк|ск|ок|вентил|шибър|чешма|бъчва|мивка|раковина|умивалник|аусгуст|казанче|wc|тоалет|моноблок|душе|батерия|смесител|сифон|тръба|фитинг|муфа|коляно|тройник|отвод|накрайник|резба|уплътн|тефлон|лента)' "
            "order by code limit 120"))).fetchall()
        print(f"=== plumbing-ish rows ({len(rows)}) ===")
        for r in rows:
            print(f"  {r[0][:38]:40} {str(r[1])[:6]:7} {str(r[2])[:8]:9} {r[3]}")


asyncio.run(main())
