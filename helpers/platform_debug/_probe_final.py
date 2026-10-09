import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\lab\tenderops\tenderops\app")
import pipeline

db = r"C:\lab\tenderops\tenderops\tenderops.sqlite3"
probes = [
 ("СКИ ф1", "бр."), ("СК ф1/2", "бр."), ("ПС 17/17", "бр."), ("ПС 50", "бр."),
 ("шлайфана настилка 5см", "м2"), ("шлайфана настилка 10см", "м2"),
 ("гранит стълби", "м2"), ("укрепваща конструкция", "м3"),
 ("Монтаж на водомер", "бр."), ("СМР-РАЗПРЕДЕЛЕНИЕ КОТА +0.80", "м2"),
 ("Термопомпен чилър въздух/вода 60 kW", "бр."), ("Термопомпен чилър въздух/вода 130 kW", "бр."),
 ("Доставка и полагане на бетон С12/15 вкл. транспорт", "м3"),
 ("Разваляне на настилка от тротоарни плочи ръчно вкл. изкопа", "м2"),
]
for desc, unit in probes:
    best, score, ev = pipeline.match_cost_v2({"desc": desc, "unit": unit}, db)
    ref = (best or {}).get("ref") or (best or {}).get("origin_ref") or "-"
    print(f"{desc[:48]:50} [{unit:4}] -> {ev.get('method'):16} {score:6.1f} {str(ref)[:70]}")
