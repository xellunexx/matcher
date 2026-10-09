import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "select code, unit, rate, substr(description,1,110), source "
            "from oe_costs_item where is_active and code in "
            "('BUILDLY-SMR-ЕЛР-0015','webanchors-186-fbc566bda61',"
            "'BUILDLY-SMR-БЛ24.212','BUILDLY-SMR-ЕЛР-0118','BUILDLY-SMR-БЛ10.015',"
            "'BUILDLY-SMR-БЛ18.048','BUILDLY-SMR-БЛ18.056','BUILDLY-MATERIAL-ЕЛ-0310',"
            "'BUILDLY-MATERIAL-МАТ-338','BUILDLY-MATERIAL-МАТ-669','BUILDLY-MATERIAL-ОБЩ-1158',"
            "'BUILDLY-MATERIAL-ЕЛ-0538','BUILDLY-MATERIAL-ЕЛ-0492','WEB-TOR-NPK','WEB-MCB6',"
            "'BUILDLY-MATERIAL-GOVSCR-0064','BUILDLY-MATERIAL-GOVSCR-0017','BUILDLY-MATERIAL-ЕЛ-2896',"
            "'BUILDLY-SMR-БЛ24.271','BUILDLY-SMR-БЛ24.344','BUILDLY-SMR-БЛ02.024','BUILDLY-SMR-БЛ10.050',"
            "'BUILDLY-SMR-ВИК-0004','BUILDLY-MATERIAL-МАТ-603','BUILDLY-MATERIAL-ОСВ-0036',"
            "'BUILDLY-MATERIAL-МАТ-252','BUILDLY-MATERIAL-МАТ-106','BUILDLY-LABOR-ТРД-033',"
            "'BUILDLY-MATERIAL-ОБЩ-1742','BUILDLY-MATERIAL-ПОКР-0535','BUILDLY-MATERIAL-МАТ-588',"
            "'BUILDLY-SMR-БЛ21.215','BUILDLY-SMR-БЛ14.001','BUILDLY-MATERIAL-ОСВ-0530',"
            "'BUILDLY-SMR-ДОВ-0088','BUILDLY-MATERIAL-МАТ-929','BUILDLY-MATERIAL-ОТОП-0020') "
            "order by code"
        ))).fetchall()
        for r in rows:
            print(f"{r[0][:38]:40} {str(r[1])[:5]:6} {str(r[2])[:9]:10} {str(r[4])[:12]:14} {r[3]}")
        print("\n--- generic cable rows (non-удължител) ---")
        rows2 = (await s.execute(text(
            "select code, unit, rate, substr(description,1,95) from oe_costs_item "
            "where is_active and lower(description) like '%кабел%' "
            "and lower(description) !~ '(удължител|обувка|розетка|канал|детектор|рейк|накрайник|ръкав|инструмент|лепенк)' "
            "order by code limit 25"
        ))).fetchall()
        for r in rows2:
            print(f"{r[0][:38]:40} {str(r[1])[:5]:6} {str(r[2])[:9]:10} {r[3]}")


asyncio.run(main())
