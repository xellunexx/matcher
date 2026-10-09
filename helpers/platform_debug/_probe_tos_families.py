import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
c = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
c.row_factory = sqlite3.Row
for pat, label in [
    ("%СФЕР%", "сферични"), ("%ТРОТОАРЕН%", "тротоарни"), ("%ПОЖАРЕН%", "пожарни"),
    ("%ИЗПРАЗН%", "изпразнител"), ("%спирателн%", "спирателни"),
    ("%сифон%", "сифони"), ("%пердаш%", "пердашена"), ("%шлайф%", "шлайфана"),
    ("%водомер%", "водомер"), ("%рекуперат%", "рекуператор"),
    ("%димен%датчик%", "димен датчик"), ("%Метални конструкц%", "метални конструкции"),
    ("%укрепв%", "укрепващи"), ("%тройник%", "тройници"), ("%дограма%", "дограма"),
]:
    print(f"--- {label} ---")
    for r in c.execute(
        "select code,desc,unit,amount_eur,source_key,status from cost_items "
        "where desc like ? order by source_key,code limit 12", (pat,)):
        print(f"  [{r['status'][:4]}] {r['code']} {str(r['desc'])[:52]} {r['unit']} {r['amount_eur']} <{r['source_key']}")
