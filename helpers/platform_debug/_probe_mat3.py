import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        for fam, cond in {
            "клапа ANY order": "lower(description) ~ '(клапа|клапан|обратн|възврат)' and lower(description) !~ '(вентилатор|решетка|конектор|въздуховод|съединител)'",
            "сифон ALL": "lower(description) ~ '(сифон|подов сиф)'",
            "филтър ВиК": "lower(description) ~ '(филтър|кос|мрежест|калотка)' and lower(description) !~ '(вентил|карбон|hepa|кутия|себест|маслен|въздушен|горив)'",
            "водомер материал": "lower(description) ~ '(водомер)'",
            "шахта/рш": "lower(description) ~ '(рш|шахт|ревизион|корито|конус)'",
            "шибърен/затвoar": "lower(description) ~ '(шибър|затвор|шк)'",
            "вентил към кран": "lower(description) ~ '(вентил|вентила)' and lower(description) !~ '(вентилат|решетк|въздух)'",
            "пожарен кран/шкаф": "lower(description) ~ '(пожар|пк |пк-|пк\\.|хидрант|пожарогас)'",
            "мачта/мълни": "lower(description) ~ '(мачт|мълни|гръмо|мълние|зазем|приемн)'",
            "изпитване/контрол смр": "lower(description) ~ '(изпитв|измерв|контрол|проверк|прозвън|регулир|проба)' and code ~ 'SMR|LABOR'",
        }.items():
            rows = (await s.execute(text(
                "select code, unit, rate, substr(description,1,88), is_active from oe_costs_item "
                f"where {cond} and coalesce(rate,'')<>'' order by is_active desc, code limit 25"))).fetchall()
            print(f"\n### {fam} ({len(rows)}) ###")
            for r in rows:
                act = "ON " if r[4] else "off"
                print(f"  {act} {r[0][:38]:40} {str(r[1])[:6]:7} {str(r[2])[:8]:9} {r[3]}")


asyncio.run(main())
