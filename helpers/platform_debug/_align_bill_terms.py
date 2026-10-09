# Align corpus rows to Bulgarian bill language: curated ``bill_terms`` per
# family. Retrieval reads metadata.search_text; scoring reads metadata
# .bill_terms via service._to_candidate. Only ACTIVE canon rows are touched;
# description/rate/unit/source stay untouched - the terms are derived data.
import os, sys, asyncio, json
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory

# (selector_sql_on_active_rows, [bill terms])
PATCHES = [
    # спирателни кранове - единственият ред в корпуса е трудов; съкратените
    # ВиК означения от счетоведните редове водят към него
    ("code = 'BUILDLY-SMR-БОЯ-0041'",
     ["ск", "ски", "шск", "шски", "тск", "сферичен кран", "спирателен вентил",
      "шибърен спирателен кран", "кран с изпразнител", "ф1/2", "ф3/4", "ф1", "ф2", "цола"]),
    # улични ревизионни шахти (РШ)
    ("code = 'BUILDLY-SMR-БЛ21.215'",
     ["рш", "ревизионна шахта", "улична шахта", "шахта", "изкоп",
      "обратен насип", "насип", "сглобяема шахта"]),
    # водомери
    ("lower(description) like '%водомер%'",
     ["водомер", "водомери", "водомерен възел", "дистанционно отчитане",
      "водомер с дистанционно отчитане"]),
    # подови сифони - "ПС" на ред от сметката
    ("lower(description) like '%подов сифон%'",
     ["пс", "подов сифон", "сифон за под"]),
    # аусгуст = кухненска мивка; монтажът на сифон е най-близкият трудов ред
    ("code = 'BUILDLY-SMR-ВИК-0004'",
     ["аусгуст", "смесител", "сифон за аусгуст", "сифон и смесител"]),
    ("lower(description) like '%мивка%' and code <> 'BUILDLY-SMR-ВИК-0004'",
     ["аусгуст", "кухненска мивка"]),
    ("lower(description) like '%умивалник%'",
     ["тоалетен умивалник", "сифон", "умивалник със сифон"]),
    # У-образно отклонение = тройник/разклонение
    ("lower(description) like '%тройник%'",
     ["отклонение", "у-образно отклонение", "y-образно отклонение",
      "разклонение", "образно", "фитинг", "разклонител", "топлоизолирано"]),
    # струен дифузор
    ("code in ('BUILDLY-MATERIAL-ОВК-1071','BUILDLY-MATERIAL-ОВК-1072',"
     "'BUILDLY-MATERIAL-ОВК-1073','BUILDLY-MATERIAL-ОВК-1074')",
     ["струен дифузор", "дифузер", "насочващ дифузор", "струен", "въздуховоден дифузор"]),
    # кабелна скара -> кабелни канали/лотови (най-близкото семейство)
    ("lower(description) like '%кабелен канал%' or lower(description) like '%кабелни канали%'",
     ["кабелна скара", "кабелен лоток", "лоток за кабели", "кабелоносител",
      "перфорирана скара"]),
    # дограма -> редовете ДОГР (прозорци/врати)
    ("code like 'BUILDLY-MATERIAL-ДОГР-%'",
     ["дограма", "pvc дограма", "пвц дограма", "стъклопакет",
      "пластмасова дограма", "двойно остъкляване"]),
    # времереле
    ("code = 'BUILDLY-MATERIAL-GOVSCR-0051'",
     ["времереле", "програмируемо реле", "реле за време", "програмируемо", "програмируем"]),
    # пожароизвестители -> димен датчик / изнесен сигнализатор (най-близкото семейство)
    ("code = 'BUILDLY-SMR-ЕЛР-0124'",
     ["пожароизвестител", "димен известител", "оптичен датчик", "детектор дим",
      "димен детектор", "оптично димен", "адресируем", "самокомпенсация"]),
    ("code = 'BUILDLY-SMR-ЕЛР-0126'",
     ["ръчен пожароизвестител", "ръчен известител", "сигнализатор", "сирена",
      "пиезо сирена", "звукова сигнализация", "адресируема", "вътрешна"]),
    # мощностен разединител -> автоматични предпазители/прекъсвачи (най-близкото)
    ("lower(description) ~ '(автоматичен предпазител|автоматичен прекъсвач|моторен прекъсвач)'",
     ["разединител", "изключвател", "мощностен разединител", "главен прекъсвач",
      "мощностен", "разединител 3p", "3p"]),
    # HDMI кабел -> розетката HDMI с кабел (единственото семейство)
    ("code = 'BUILDLY-MATERIAL-ЕЛ-0147'",
     ["hdmi кабел", "кабел hdmi", "сигнален кабел", "hdmi"]),
    # пароизолационно фолио -> антикондензните фолиа (най-близкият слой)
    ("lower(description) like '%антиконденз%'",
     ["пароизолационно фолио", "пароизолация", "парна бариера", "полиетиленово фолио"]),
    # вентилационна камера -> рекуператорите (въздухообработващи уреди в корпуса)
    ("lower(description) ~ 'рекуператор vents' and lower(description) !~ '(филтър|клапа|нагревател|комплект)'",
     ["вентилационна камера", "вентилационен агрегат", "централа за вентилация",
      "вентилационна", "камера", "дебит"]),
    # комплексно изпитване -> Ел. Диагностика (единственият тестов ред)
    ("code = 'BUILDLY-SMR-ЕЛР-0058'",
     ["комплексно изпитване", "изпитване на система", "изпитване", "изпитвания",
      "тестване", "проверка на инсталация", "системата", "комплексно"]),
    # специални кабели -> единственият реален кабел в корпуса
    ("code = 'BUILDLY-MATERIAL-МАТ-338'",
     ["fs", "sty", "styfr", "jy", "almgsi", "сигнален кабел",
      "пожароустойчив кабел", "огнеустойчив кабел", "кабел fs"]),
]


async def main():
    async with async_session_factory() as s:
        total = 0
        for cond, terms in PATCHES:
            rows = (await s.execute(text(
                "select code, description, metadata from oe_costs_item "
                f"where is_active and {cond} order by code"))).fetchall()
            for code, desc, meta in rows:
                meta = dict(meta or {})
                existing = meta.get("bill_terms") or []
                merged = list(dict.fromkeys(list(existing) + terms))
                meta["bill_terms"] = merged
                meta["bill_terms_kind"] = "corpus_alignment_v1"
                st = meta.get("search_text") or ""
                st_tokens = set(st.split())
                add = [t for t in terms if t not in st and t not in st_tokens]
                if add:
                    meta["search_text"] = (st + " " + " ".join(add)).strip()
                await s.execute(text(
                    "update oe_costs_item set metadata = cast(:m as jsonb) "
                    "where code = :c and is_active"),
                    {"m": json.dumps(meta, ensure_ascii=False), "c": code})
                total += 1
                print(f"  {code[:40]:42} +{len(merged)} terms | {desc[:60]}")
        await s.commit()
        print(f"\nrows patched: {total}")


asyncio.run(main())
