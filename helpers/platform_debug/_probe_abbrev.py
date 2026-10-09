import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        for fam, cond in {
            "кран/клапан/вентил материал": (
                "lower(description) ~ '(кран|клапа|клапан|вентил|шибър|сфер)' "
                "and lower(description) !~ '(под наем|автокран|верижни|мобилен|вентилатор|решетка|душ|батерия|смесител|бъчва)'"),
            "обратна/възвратна клапа вода": (
                "lower(description) ~ '(обратна клапа|обратен клапан|възвратна|възвратен|обр. клапа|non.return)'"),
            "сифон": "lower(description) ~ '(сифон|подов сиф|пс\\d|подов)'",
            "филтър вода/кос": (
                "lower(description) ~ '(филтър|филтри|кос фил|мрежест|калот)' "
                "and lower(description) !~ '(вентил|карбон|hepa|g4|f8|кутия)'"),
            "водомер/водомерен": "lower(description) ~ '(водомер|водомерн)'",
            "пожароизв/сирена/датчик дим": (
                "lower(description) ~ '(пожар|димен|димен|известител|сирена|оптичен датчик|датчик за дим|mcp|call point)'"),
            "изпитване/проверка/измерване": (
                "lower(description) ~ '(изпитван|измерван|проверка|прозвъняване|диагностика|протокол)'"),
            "зазем/мълния/гръм": "lower(description) ~ '(зазем|мълни|гръмо|приемн|импеданс|преходно)'",
            "шахта/рш/ревиз": "lower(description) ~ '(шахт|рш|ревизион)'",
            "разединител/изключвател": "lower(description) ~ '(разединител|изключвател|главен|ins |isw|is-)'",
            "лоток/скара/перфориран": "lower(description) ~ '(лоток|скара|перфориран|кабелен канал|кабелна)'",
            "дифузор/анемостат": "lower(description) ~ '(дифузор|дифузер|анемостат|струен|jet)'",
            "фолио/пароизол": "lower(description) ~ '(фолио|пароизол|парна|паронепр|мембрана|антиконденз)'",
            "аусгуст/мивка/умивалник": "lower(description) ~ '(аусгуст|мивк|умивалник|мойка)'",
            "фреон/зареждане": "lower(description) ~ '(фреон|хладилен|r410|r32|зареждане на клим|зареждане)'",
            "вент камера/централа": "lower(description) ~ '(камера|централа|рекуператор|проветрител)'",
            "оберлихт/купол": "lower(description) ~ '(оберлихт|купол|светлинен|покривен прозорец|светъл)'",
            "крепеж комплект": "lower(description) ~ '(крепеж|комплект крепеж|монтажен комплект|конзол|стойка|укрепв)'",
        }.items():
            rows = (await s.execute(text(
                "select code, unit, rate, substr(description,1,88) from oe_costs_item "
                f"where is_active and coalesce(rate,'')<>'' and {cond} "
                "order by code limit 16"))).fetchall()
            print(f"\n### {fam} ({len(rows)}) ###")
            for r in rows:
                print(f"  {r[0][:38]:40} {str(r[1])[:6]:7} {str(r[2])[:8]:9} {r[3]}")


asyncio.run(main())
