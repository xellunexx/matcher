import sys, io, tempfile, shutil
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb, pipeline

db = ROOT / "tenderops.sqlite3"
QUERIES = [
    ("Шум около пейки обувки пясък " * 26 + "остъкляване с 3 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("СК ф1/2", "бр."),
    ("Доставка и монтаж на подов сифон ПС 17/17", "бр."),
    ("Полагане на гранит-стълби", "м2"),
    ("Доставка и полагане на бетон С12/15 вкл. транспорт и всички разходи", "м3"),
    ("Изработка и монтаж на укрепваща метална конструкция", "кг"),
    ("Демонтаж на теракотни плочки", "м2"),
    ("Доставка и монтаж на тръба ППр ф25", "м"),
    ("Термопомпен чилър въздух/вода 60 kW, работни граници до -20", "бр."),
    ("ОСТЪКЛЯВАНЕ С 3 ММ СТЪКЛА В/У ДЪРВЕНИ РАМКИ", "м2"),
    ("Разбиване на неармирана бетонна настилка ръчно с почистване", "м2"),
    ("Доставка, монтаж и изпитване на пожарен кран окомплектован", "бр."),
    ("Монтаж на спирателен кран СКИ ф1", "бр."),
]
for q, u in QUERIES:
    raw = costdb.search_candidates(db, q, u, limit=40, include_pending=True)
    scored = []
    for r in raw:
        v = pipeline._cost_row_view(r)
        s, mth, det = pipeline._candidate_score({"desc": q, "unit": u}, v)
        scored.append((s, v, det, mth))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        print(f"{q[:46]:48} -> (no pool)")
        continue
    s, v, det, mth = scored[0]
    f = det.get("factors", {})
    print(f"{q[:44]:46} -> {str(v['desc'])[:40]:42} s={s:5.1f} {v['status'][:4]} cov={f.get('query_coverage')} mt={f.get('matched_tokens')} m={mth}")
