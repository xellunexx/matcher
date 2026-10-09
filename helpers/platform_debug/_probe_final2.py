import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        for fam, cond in {
            "рекуператор units": "lower(description) ~ '(рекуператор|проветрител|вентилационен блок|airbox|micra|vuts|vute)' and lower(description) !~ '(филтър|клапа|нагревател)'",
            "вентилатор канален": "lower(description) ~ '(канален вентилатор|вентилатор за канал|покривен вентилатор|центробежен вентилатор)'",
            "кран вика материал": "lower(description) ~ '(спирателен|сферичен кран|шибърен|сферична канела|кран ф|кран 1/2|вентил ф|обратна клапа)' and lower(description) !~ '(кран под наем|автокран|верижни|мобилен кран|друг кран|смесител|батерия)'",
            "шкаф/rack": "lower(description) ~ '(шкаф|rack|рак|19)' and lower(description) !~ '(шкафове за|кухненски шкаф|гардероб)'",
            "контактор/пускател": "lower(description) ~ '(контактор|пускател|команден)'",
            "усилвател/аудио": "lower(description) ~ '(усилвател|тонколона|озвуч|аудио|плеър|микрофон)'",
            "nas/диск/vga": "lower(description) ~ '(nas|твърд диск|hdd|ssd|видеорекордер|dvr|nvr)'",
            "фреон/хладилен": "lower(description) ~ '(фреон|хладилен|r410|r32|r134)'",
            "оберлихт": "lower(description) ~ '(оберлихт|светлинен купол|светлокупол|покривен прозорец)'",
        }.items():
            rows = (await s.execute(text(
                "select code, unit, rate, substr(description,1,92) from oe_costs_item "
                f"where is_active and coalesce(rate,'')<>'' and {cond} order by code limit 12"
            ))).fetchall()
            print(f"\n--- {fam} ({len(rows)}) ---")
            for r in rows:
                print(f"  {r[0][:36]:38} {str(r[1])[:5]:6} {str(r[2])[:9]:10} {r[3]}")


asyncio.run(main())
