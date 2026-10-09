import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\lab\tenderops\tenderops\app")
import pipeline

db = r"C:\lab\tenderops\tenderops\tenderops.sqlite3"
rows = [
 {"desc": "Разваляне на настилка от тротоарни плочи ръчно вкл. изкопа", "unit": "м2", "qty": 10},
 {"desc": "Емоджи риск доставка на материали за площадката работи", "unit": "бр.", "qty": 7},
 {"desc": "Дата вместо номер", "unit": "бр.", "qty": 1},
 {"desc": "Нормална позиция след шума във файла", "unit": "м2", "qty": 42},
 {"desc": "Позиция без заглавна редова рамка", "unit": "бр.", "qty": 3},
 {"desc": "Втора позиция също без заглавие", "unit": "бр.", "qty": 4},
]
for r in rows:
    best, score, ev = pipeline.match_cost_v2(r, db)
    print(r["desc"][:55], "[", r["unit"], "] ->", ev.get("method"), "score=", score,
          "ref=", (best or {}).get("ref") or (best or {}).get("origin_ref"),
          "status=", (best or {}).get("status"), "eur=", (best or {}).get("amount_eur"))
    for c in (ev.get("top_candidates") or [])[:3]:
        print("    cand:", c.get("ref") or c.get("origin_ref"), "score=", c.get("score"),
              "status=", c.get("status"), "eur=", c.get("amount_eur"), "desc=", str(c.get("desc"))[:70])
