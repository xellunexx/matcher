"""Corpus alias alignment pass 2 — Bulgarian bill abbreviations onto family rows.

Every term here is real estimator/bill vocabulary for the item the row actually
describes (СК = спирателен кран, ПС = подов сифон, ТСК = тротоарен спирателен
кран, СКИ = спирателен кран с изпразнител, ПК = пожарен кран). Aliases only make
the row reachable + scoreable; the suggestion still shows the real description.
"""
import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory

ALIASES = {
    # спирателен кран / сферичен кран (inch sizes)
    "МАТ-434": ["ск", "спирателен кран", "сферичен кран", "шаров кран", "кран ф1/2", "кран 1/2"],
    "МАТ-435": ["ск", "спирателен кран", "сферичен кран", "шаров кран", "кран ф1", "кран 1"],
    "МАТ-436": ["ск", "спирателен кран", "сферичен кран", "шаров кран", "кран ф3/4", "кран 3/4"],
    # сферични ППр (mm sizes) — generic СК surface
    "МАТ-437": ["ск", "спирателен кран", "сферичен кран", "кран ппр"],
    "МАТ-438": ["ск", "спирателен кран", "сферичен кран", "кран ппр"],
    "МАТ-439": ["ск", "спирателен кран", "сферичен кран", "кран ппр", "кран ф2"],
    # спирателни кранове mm (flanged, 60-150мм)
    "МАТ-430": ["ск", "спирателен кран", "кран фланцов"],
    "МАТ-431": ["ск", "спирателен кран", "кран фланцов"],
    "МАТ-432": ["ск", "спирателен кран", "кран фланцов", "кран ф2"],
    "МАТ-433": ["ск", "спирателен кран", "кран фланцов"],
    # спирателен кран с изпразнител = СКИ
    "МАТ-426": ["ски", "кран с изпразнител", "спирателен кран с изпразняване", "кран с изпразнителна арматура"],
    "МАТ-427": ["ски", "кран с изпразнител", "спирателен кран с изпразняване", "кран ф2"],
    # тротоарен спирателен кран = ТСК
    "МАТ-440": ["тск", "тротоарен кран", "тротоарен спирателен кран", "кран ф1"],
    "МАТ-441": ["тск", "тротоарен кран", "тротоарен спирателен кран", "кран ф2"],
    # пожарен кран = ПК
    "МАТ-424": ["пк", "пожарен кран", "пожарникарски кран", "окомплектован пожарен кран", "кран ф2"],
    "МАТ-425": ["пк", "пожарен кран", "пожарникарски кран", "окомплектован пожарен кран"],
    # подов сифон = ПС
    "СУХ-0140": ["пс", "подов сифон", "сифон за под", "сифон за тераса"],
    "СУХ-0145": ["пс", "подов сифон", "сифон за под", "сифон за баня", "сифон 17/17", "пс 17"],
    "СУХ-0146": ["пс", "подов сифон", "сифон за под", "сифон за тераса"],
    "СУХ-0150": ["пс", "подов сифон", "сифон за под", "сифон за баня"],
    # метална/укрепваща конструкция
    "СМР-0024": ["укрепваща конструкция", "стоманена конструкция", "метална конструкция", "конструкция за монтаж"],
    # сферичен мини кран — still a спирателен кран
    "ОБЩ-1197": ["ск", "спирателен кран", "мини кран", "сферичен кран"],
}


async def main():
    async with async_session_factory() as s:
        patched = 0
        for code_frag, terms in ALIASES.items():
            rows = (await s.execute(text(
                "select id, code from oe_costs_item where is_active and code like :c"),
                {"c": f"%{code_frag}"})).fetchall()
            for rid, code in rows:
                await s.execute(text(
                    "update oe_costs_item set metadata = "
                    "  jsonb_set("
                    "    jsonb_set("
                    "      jsonb_set(coalesce(metadata,'{}'::jsonb), '{bill_terms}',"
                    "        coalesce(metadata->'bill_terms','[]'::jsonb) || cast(:t as jsonb), true),"
                    "      '{alias_kind}', '\"bill_abbrev\"'::jsonb, true),"
                    "    '{search_text}',"
                    "    to_jsonb(coalesce(metadata->>'search_text','') || ' ' || :plain), true) "
                    "where id = :id"),
                    {"t": __import__("json").dumps(terms, ensure_ascii=False),
                     "plain": " ".join(terms), "id": rid})
                patched += 1
        await s.commit()
        print(f"patched {patched} rows")


asyncio.run(main())
