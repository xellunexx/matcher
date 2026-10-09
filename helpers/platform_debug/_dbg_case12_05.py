import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import pipeline
db = ROOT / "tenderops.sqlite3"
for q, u in [
    ("Термопомпен чилър въздух/вода 60 kW, работни граници до -20", "бр."),
    ("Доставка и полагане на бетон С12/15 вкл. транспорт и всички разходи", "м3"),
    ("Разбиване на неармирана бетонна настилка ръчно с почистване", "м2"),
    ("Остъкляване с 3 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("Остъкляване със стъкла върху дървени рамки при ремонти", "м2"),
]:
    best, score, ev = pipeline.match_cost_v2({"desc": q, "unit": u, "qty": 1}, db)
    print(f"{q[:48]:50} -> {(best or {}).get('ref')} | {score} | {ev.get('method')} | gate={ev.get('gate')}")
    for c in ev.get("top_candidates", [])[:4]:
        print(f"      {c['score']:6} {str(c['status'])[:4]:4} {str(c['desc'])[:55]}")
