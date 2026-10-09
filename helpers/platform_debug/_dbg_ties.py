import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb, pipeline
db = ROOT / "tenderops.sqlite3"
for q, u in [
    ("Остъкляване с 3 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("Остъкляване с 4 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("Остъкляване със стъкла върху дървени рамки при ремонти", "м2"),
    ("Разбиване на неармирана бетонна настилка ръчно с почистване", "м2"),
]:
    raw = costdb.search_candidates(db, q, u, limit=40, include_pending=True)
    scored = []
    for r in raw:
        v = pipeline._cost_row_view(r)
        s, m, d = pipeline._candidate_score({"desc": q, "unit": u}, v)
        scored.append((s, m, v, d))
    scored.sort(key=lambda x: (-x[0], x[2]["status"] != "active"))
    print("###", q[:50])
    for s, m, v, d in scored[:6]:
        print(f"   {s:5.1f} {v['status'][:4]:4} {str(v['unitEur']):9} {m:18} {v['ref'][:30]:32} {str(v['desc'])[:45]}")
