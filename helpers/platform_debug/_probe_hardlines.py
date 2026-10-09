import importlib.util, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = r"C:\lab\tenderops\tenderops"

def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + rf"\app\{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m

cdb = mod("costdb"); pipe = mod("pipeline")
db = ROOT + r"\tenderops.sqlite3"

queries = [
    ("СК ф1/2", "бр"), ("СК ф2", "бр"), ("СКИ ф1", "бр"), ("ТСК ф2", "бр"),
    ("ПК окомплектован", "бр"), ("ПС 17/17", "бр"),
    ("Доставка и монтаж на подов сифон ПС 17/17", "бр"),
    ("Изграждане на шлайфана армирана циментова настилка, деб. 10 см", "м2"),
    ("Полагане на гранит-стълби", "м2"),
    ("Монтаж на водомер с дистанционно отчитане", "бр"),
    ("СМР-РАЗПРЕДЕЛЕНИЕ КОТА +0.80", "бр"),
    ("Изработка и монтаж на укрепваща метална конструкция", "кг"),
    ("Демонтаж на теракотни плочки", "м2"),
    ("Доставка и монтаж на тръба ППр ф25", "м"),
]
for q, u in queries:
    best, score, ev = pipe.match_cost_v2({"desc": q, "unit": u, "qty": 1}, db)
    tag = "COMMIT" if best else ("PROPOSE" if ev.get("proposal") else "BLANK ")
    info = best or ev.get("proposal") or {}
    tops = " | ".join(f"{t['desc'][:30]}({t['score']:.0f},{(t.get('status') or '?')[:4]})" for t in ev.get("top_candidates", [])[:3])
    print(f"{tag} {score:6.1f} [{ev.get('method')}/{ev.get('confidence')}] {q[:44]}")
    print(f"        -> {str(info.get('desc'))[:60]} | {info.get('unitEur')} €/{info.get('unit')} | {str(info.get('sourceKey'))}")
    print(f"        top: {tops}")
