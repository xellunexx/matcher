import sys, io, tempfile, shutil, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(r"C:\lab\tenderops\tenderops")
sys.path.insert(0, str(ROOT / "app"))
import costdb, pipeline, matcher

db = ROOT / "tenderops.sqlite3"
q8 = "Шум около пейки обувки пясък " * 26 + "остъкляване с 3 мм стъкла върху дървени рамки при ремонти"
q4 = "ОСТЪКЛЯВАНЕ С 3 ММ СТЪКЛА В/У ДЪРВЕНИ РАМКИ"

for q in (q8, q4):
    q_content = (set(matcher.canonical_tokens(costdb._domain_norm(q)))
                 - matcher._PROCESS_CONCEPTS - matcher._GENERIC_TERMS)
    raw = costdb.search_candidates(db, q, "м2", limit=40, include_pending=True)
    scored = []
    for r in raw:
        v = pipeline._cost_row_view(r)
        s, m, d = pipeline._candidate_score({"desc": q, "unit": "м2"}, v)
        scored.append((s, m, v, d))
    scored.sort(key=lambda x: (-x[0], x[2]["status"] != "active"))
    print("###", q[:55], "| q_content:", len(q_content), sorted(q_content))
    for s, m, v, d in scored[:6]:
        f = d.get("factors", {})
        print(f"   {s:5.1f} {v['status'][:4]:4} {m:18} cov={f.get('query_coverage')} mt={f.get('matched_tokens')} spec={f.get('spec_match')} {v['ref'][:28]:30} {str(v['desc'])[:42]}")
