import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")
c = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
c.row_factory = sqlite3.Row
print("items:", c.execute("select count(*) from cost_items").fetchone()[0],
      "active:", c.execute("select count(*) from cost_items where status='active'").fetchone()[0])
print("sources:")
for r in c.execute("select source_key, count(*) n from cost_items group by 1 order by 2 desc"):
    print(" ", r["source_key"], r["n"])
for pat in ("%кран%", "%сифон%", "%клапан%", "%пердаш%", "%замазк%",
            "%латекс%", "%фаянс%", "%гранит%", "%водомер%", "%конструкц%",
            "%скеле%", "%тръб%", "%рекуперат%"):
    n = c.execute("select count(*) from cost_items where desc like ? and status='active'",
                  (pat,)).fetchone()[0]
    print(f"{pat}: {n}")
print("--- кран rows (active, first 35) ---")
for r in c.execute(
    "select code,desc,unit,amount_eur,source_key from cost_items "
    "where desc like '%кран%' and status='active' order by code limit 35"):
    print(" ", r["code"], str(r["desc"])[:58], r["unit"], r["amount_eur"], r["source_key"])
print("--- сифон rows ---")
for r in c.execute(
    "select code,desc,unit,amount_eur,source_key from cost_items "
    "where desc like '%сифон%' and status='active' order by code limit 15"):
    print(" ", r["code"], str(r["desc"])[:58], r["unit"], r["amount_eur"], r["source_key"])
