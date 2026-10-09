import importlib.util, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
spec = importlib.util.spec_from_file_location("matcher", r"C:\lab\tenderops\tenderops\app\matcher.py")
m = importlib.util.module_from_spec(spec); sys.modules["matcher"] = m; spec.loader.exec_module(m)
for q, c, qu, cu in [
    ("Полагане на гранит-стълби", "Полагане на гранитогресни плочи", "м2", "M2"),
    ("СК ф1/2", 'КРАН СФЕР. 1/2" ск спирателен кран', "бр", "PIECE"),
    ("шлайфана настилка 10 см", "Циментова замазка до 5 см армирана пердашена", "м2", "M2"),
    ("ПС 17/17", "Подов сифон за баня пс", "бр", "PIECE"),
    ("Монтаж на водомер с дистанционно отчитане", "Монтаж на водомер водомер водомери водомерен възел дистанционно отчитане", "бр", "PIECE"),
    ("Демонтаж на теракотни плочки", "Полагане на теракотни плочки", "м2", "M2"),
    ("СМР-РАЗПРЕДЕЛЕНИЕ КОТА +0.80", "Ел. табло комплектно", "бр", "PIECE"),
]:
    ms = m.score_match(q, c, query_unit=qu, candidate_unit=cu)
    print(f"{q[:42]:44} -> {ms.confidence:.3f} {ms.band:6} {ms.reasons}")
print("canon(шлайфана настилка):", m.canonical_tokens("шлайфана настилка"))
print("units м2/M2:", m.units_compatible("м2", "M2"), "| м2/PIECE:", m.units_compatible("м2", "PIECE"), "| бр/PIECE:", m.units_compatible("бр", "PIECE"))
