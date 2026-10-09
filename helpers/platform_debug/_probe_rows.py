import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory

QUERIES = {
    "подов сифон": "lower(description) like '%сифон%'",
    "кран спирател/сфера/шибър": "lower(description) ~ '(спирател|сферичен|шибър|кран)'",
    "клапа обратна/възвратна": "lower(description) ~ '(обратн|възвратн)'",
    "водомер": "lower(description) like '%водомер%'",
    "мивка": "lower(description) ~ '(мивк|умивалник)'",
    "тройник/разклон/Y": "lower(description) ~ '(тройник|разклон|y-образ|у-образ|тетка)'",
    "дограма pvc": "lower(description) ~ '(дограма|pvc|пвц)'",
    "лоток/скара кабелна": "lower(description) ~ '(лоток|скара|кабелен канал|кабелна)'",
    "дифузор": "lower(description) ~ '(дифузор|струен)'",
    "прекъсвач/разединител/аппарат": "lower(description) ~ '(прекъсвач|разединител|апарат)'",
    "реле/таймер": "lower(description) ~ '(реле|таймер|времереле)'",
    "камера вент/рекуператор": "lower(description) ~ '(камера|рекуператор|вентилацион)'",
    "шахта": "lower(description) like '%шахт%'",
    "клапан конденз/източв": "lower(description) ~ '(конденз|източв|изпразнител|изпускател)'",
    "детектор/пожар": "lower(description) ~ '(детектор|пожар|димен|оптичн)'",
    "укреп/конзол/стойка": "lower(description) ~ '(укрепв|конзол|стойка|конструкция)'",
    "HDMI/сигнал кабел": "lower(description) ~ '(hdmi|сигнален|utp|ftp|коаксиал)'",
}


async def main():
    async with async_session_factory() as s:
        for fam, cond in QUERIES.items():
            q = text(
                "select code, unit, rate, currency, substr(description,1,95) "
                f"from oe_costs_item where is_active and coalesce(rate,'') <> '' and {cond} "
                "order by code limit 14")
            rows = (await s.execute(q)).fetchall()
            print(f"\n=== {fam} ({len(rows)} shown) ===")
            for r in rows:
                print(f"  {r[0][:34]:36} {str(r[1])[:5]:6} {str(r[2])[:8]:9} {r[4]}")


asyncio.run(main())
