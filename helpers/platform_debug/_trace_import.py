import sys, io, asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
from app.modules.boq.importers.excel import ExcelImporter

content = open(r"C:\lab\tenderops\KSS_ocenena_EST20261005_230631.xlsx", "rb").read()
res = asyncio.run(ExcelImporter.parse(content))
pos = res.positions
print("positions:", len(pos), "| errors:", len(res.errors), "| warnings:", len(res.warnings))

leaf = [p for p in pos if not getattr(p, "is_section", False)]
secs = [p for p in pos if getattr(p, "is_section", False)]
print("leaf:", len(leaf), "| sections:", len(secs))

# 1) which leaf positions have no qty -> position_has_quantity ERROR
noqty = [p for p in leaf if not (getattr(p, "quantity", 0) or 0)]
print("\n== leaf positions with qty<=0 (position_has_quantity errors):", len(noqty))
for p in noqty[:60]:
    print(f"   ord={p.ordinal!r} unit={p.unit!r} qty={p.quantity} rate={p.unit_rate} | {str(p.description)[:60]}")

# 2) unit_rate_in_range: median + flagged
rates = [float(p.unit_rate) for p in leaf if p.unit_rate and float(p.unit_rate) > 0]
rates.sort()
med = rates[len(rates)//2] if rates else 0
print(f"\n== unit_rate_in_range: n_rates={len(rates)} median={med} threshold={med*5}")
flagged = [p for p in leaf if p.unit_rate and float(p.unit_rate) > med * 5]
print("flagged:", len(flagged))
for p in flagged[:15]:
    print(f"   ord={p.ordinal!r} rate={p.unit_rate} unit={p.unit!r} | {str(p.description)[:60]}")
print("\n== import warnings sample:")
for w in res.warnings[:8]:
    print("  ", json.dumps(w, ensure_ascii=False)[:160] if (w:=w) else "")
import json
