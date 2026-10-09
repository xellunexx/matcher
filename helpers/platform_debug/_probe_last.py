import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        for fam, cond in {
            "фасонни части": "lower(description) ~ '(фасонн|части.*ппр|фитинг)'",
            "тава шкаф/стелаж": "lower(description) ~ '(тава|шкаф.*телеком|стелаж|rack)'",
            "кабел fire/сигн": "lower(description) ~ '(кабел)' and lower(description) ~ '(пожар|сигнал|j-y|2x|2х|двойка|screen|екран|обдиг)'",
            "hdmi кабел": "lower(description) ~ '(hdmi)' and lower(description) ~ '(кабел|удълж|преход)'",
            "воден/груб филтър": "lower(description) ~ '(филтър.*вод|вод.*филтър|груб филтър|кос филтър|магнитен филтър|промивен|self.*clean|воздухо|обезвъзд)'",
            "пк шкаф/окомплект": "lower(description) ~ '(пожарен|хидрант|окомплект|пожарникар|пожарникарски)'",
            "кутия наклон/рамп": "lower(description) ~ '(пожароизвест|димен датчик|датчик.*дим|сирена|блинкер|сполуч|табло.*пожар)'",
        }.items():
            rows = (await s.execute(text(
                "select code, unit, rate, substr(description,1,90), is_active from oe_costs_item "
                f"where {cond} order by is_active desc, code limit 20"))).fetchall()
            print(f"\n### {fam} ({len(rows)}) ###")
            for r in rows:
                act = "ON " if r[4] else "off"
                print(f"  {act} {r[0][:38]:40} {str(r[1])[:6]:7} {str(r[2])[:8]:9} {r[3]}")


asyncio.run(main())
