import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory

QUERIES = {
    "лоток кабелен": "lower(description) ~ '(лоток|корито|кабелна скара|кабелен канал|кабелоносител)'",
    "пожарен кран/шафа": "lower(description) ~ '(пожарен кран|пожарна шафа|пожарогасител|хидрант)'",
    "вакуум/вакуумиране": "lower(description) ~ '(вакуум)'",
    "пароизолация/мембрана": "lower(description) ~ '(пароизолац|парна бариера|паробар|мембрана)'",
    "филтър вода/мрежа": "lower(description) ~ '(филтър|филтри|кос филтър|мрежест)'",
    "изпитване/наладка/протокол": "lower(description) ~ '(изпитв|наладк|пусконалад|протокол|програмиран|конфигур|проверка|контрол)'",
    "кабел марки": "lower(description) ~ '(j-y|sty|almgsi|almg|свт|nYY|nyy|cyky|кабел)'",
    "крепеж/дюбел/винт": "lower(description) ~ '(крепеж|дюбел|анкер|комплект крепеж)'",
    "тава/рафт/полка": "lower(description) ~ '(тава|рафт|полица|полка)'",
    "разединител/изключвател/главен": "lower(description) ~ '(разединител|изключвател|главен прекъсвач|mcb|мощностен)'",
    "сирена/зумер": "lower(description) ~ '(сирена|зумер|звуков|оповест)'",
    "струен/jet дифузор": "lower(description) ~ '(струен|jet|насочващ|дифузор)'",
    "клапан вика": "lower(description) ~ '(клапан|клапа|вентил|кран)'",
    "прозорец/дограма": "lower(description) ~ '(прозорец|прозорци|дограма|стъклопакет|алуминиева дограма)'",
}


async def main():
    async with async_session_factory() as s:
        for fam, cond in QUERIES.items():
            q = text(
                "select code, unit, rate, substr(description,1,90) "
                f"from oe_costs_item where is_active and coalesce(rate,'') <> '' and {cond} "
                "order by code limit 10")
            rows = (await s.execute(q)).fetchall()
            print(f"\n=== {fam} ({len(rows)} shown) ===")
            for r in rows:
                print(f"  {r[0][:34]:36} {str(r[1])[:5]:6} {str(r[2])[:8]:9} {r[3]}")


asyncio.run(main())
