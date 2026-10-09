import importlib.util, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = r"C:\lab\tenderops\tenderops"
def mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT + rf"\app\{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m
cdb = mod("costdb"); pipe = mod("pipeline")
db = ROOT + r"\tenderops.sqlite3"
for q, u in [
    ("Остъкляване с 3 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("Остъкляване с 4 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("Остъкляване с 5 мм стъкла върху дървени рамки при ремонти", "м2"),
    ("СЕК12.801 Остъкляване със стъкла върху дървени рамки при ремонти", "м2"),
    ("остъкляване със стъкла върху дървени рамки по позиция СЕК12.801", "м2"),
    ("ОСТЪКЛЯВАНЕ С 3 ММ СТЪКЛА В/У ДЪРВЕНИ РАМКИ", "м2"),
    ("Разбиване на неармирана бетонна настилка ръчно с почистване", "м2"),
    ("на за от с по в до при вкл", "м2"),
    ("несъществуващ продукт люляк агитан", "бр."),
    ("Термопомпен чилър въздух/вода 60 kW", "бр."),
    ("Доставка и полагане на бетон С12/15 вкл. транспорт", "м3"),
]:
    best, score, ev = pipe.match_cost_v2({"desc": q, "unit": u, "qty": 1}, db)
    tag = "COMMIT" if best else ("PROPOSE" if ev.get("proposal") else "BLANK ")
    ref = (best or {}).get("ref") or (ev.get("proposal") or {}).get("ref")
    print(f"{tag} {score:6.1f} {q[:52]:54} -> {str(ref)[:42]} [{ev.get('method')}]")
