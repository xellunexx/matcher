import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb, pipeline

db = ROOT / "tenderops.sqlite3"
for q in ("Остъкляване с 4 мм стъкла върху дървени рамки при ремонти",
          "Остъкляване с 5 мм стъкла върху дървени рамки при ремонти",
          "Остъкляване с 3 мм стъкла върху дървени рамки при ремонти"):
    raw = costdb.search_candidates(db, q, "м2", limit=40, include_pending=True)
    scored = []
    for r in raw:
        v = pipeline._cost_row_view(r)
        s, m, d = pipeline._candidate_score({"desc": q, "unit": "м2"}, v)
        scored.append((s, m, v, d))
    scored.sort(key=lambda x: (-x[0], x[2]["status"] != "active"))
    print("###", q[:52])
    for s, m, v, d in scored[:8]:
        f = d.get("factors", {})
        print(f"   {s:5.1f} {v['status'][:4]:4} {str(v['unitEur'])[:8]:8} {m:18} mt={f.get('matched_tokens')} spec={f.get('spec_match')} ccov={f.get('candidate_coverage')} {v['ref'][:24]:26} {str(v['desc'])[:44]}")
