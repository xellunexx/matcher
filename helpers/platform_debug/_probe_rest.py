import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        for fam, cond in {
            "тройник/отклонение/разклонител": "lower(description) ~ '(тройник|отклонен|разклонител|разклонява|у-образн|трипътник)'",
            "кабел j-y / пожарен / hdmi": "lower(description) ~ '(hdmi|j-y|сигн.*кабел|кабел.*сигн|пожар.*кабел|кабел.*пожар|вита двойка|utp|ftp|2x0)'",
            "рекуператор/вент камера/централа": "lower(description) ~ '(рекуперат|вентил.*камера|вентил.*централ|свж въздух|приточно|източващ)'",
            "клапан конденз/заустване": "lower(description) ~ '(клапан|конденз|заустван|дренаж|склонител)'",
            "оберлихт/купол/покрив отвор": "lower(description) ~ '(оберлихт|купол|акрилен|светлоотвор|светл.*купол)'",
            "разединител/изключ/изолатор": "lower(description) ~ '(разедин|изключител|изолатор|откопчител|прекъсвач|автоматичен.*апарат|апарат)'",
            "укрепваща конструкция": "lower(description) ~ '(укрепв|конструкц|столб|стойка|кронщайн)'",
            "вакуум/фреон": "lower(description) ~ '(вакуум|фреон|r410|r32|r134|хладилен)'",
            "webanchors-186": "code like 'webanchors-186%'",
        }.items():
            rows = (await s.execute(text(
                "select code, unit, rate, substr(description,1,88), is_active, source from oe_costs_item "
                f"where {cond} order by is_active desc, code limit 18"))).fetchall()
            print(f"\n### {fam} ({len(rows)}) ###")
            for r in rows:
                act = "ON " if r[4] else "off"
                print(f"  {act} {r[0][:38]:40} {str(r[1])[:6]:7} {str(r[2])[:8]:9} {r[5][:14]:15} {r[3]}")


asyncio.run(main())
