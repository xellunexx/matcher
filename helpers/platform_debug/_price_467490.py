# -*- coding: utf-8 -*-
"""467490 Radomir: price the spec-derived scope against the canonical cost DB and
write the DRAFT КСС for owner approval (never a submission).

Canon on this path: codes/arithmetic by code tool only; a row gets a price ONLY when the
deterministic matcher accepts a corpus item (exact/code/high-margin semantic). Everything
else stays blank with COST_NOT_FOUND — the human gate is the draft's whitespace.
"""
import importlib.util, json, sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent


def mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


pipe = mod("pipeline")
cdb = mod("costdb")
subm = mod("submission")

rows = json.loads((ROOT / "data/demo/processed/files/467490/_kss_scope_advised.json").read_text(encoding="utf-8")) \
    if (ROOT / "data/demo/processed/files/467490/_kss_scope_advised.json").exists() \
    else json.loads((ROOT / "data/demo/processed/files/467490/_kss_scope.json").read_text(encoding="utf-8"))
db = str(cdb.db_path_for(ROOT / "data/demo"))

boq, n_csv, seconds = [], 0, []
for i, r in enumerate(rows, 1):
    line = {"key": f"R{i}", "row": i, "no": str(r["no"] or i), "section": r["section"],
            "desc": r["desc"], "unit": r["unit"], "qty": r["qty"], "flag": "", "note": ""}
    best, score, ev = pipe.match_cost_v2({"desc": r["desc"], "unit": r["unit"], "qty": r["qty"]}, db)
    if best:
        net = best["unitEur"] / 1.2 if best["vatIncluded"] else best["unitEur"]
        line.update({"rule": "CSV", "unitEur": round(net, 4), "sumEur": round(net * r["qty"], 2),
                     "note": f"{best.get('ref')} · {ev['method']} · score {score:.1f} · {ev.get('confidence')} | {r['evidence']}"})
        n_csv += 1
    else:
        prod = r.get("product")
        prod_note = (f" | кандидат-продукт (каталог, без цена): {prod['name']} · {prod['brand']}"
                     f"{(' ' + prod['series']) if prod.get('series') else ''} · {prod.get('dims') or ''}"
                     if prod else "")
        line.update({"rule": "EST", "unitEur": None, "sumEur": None, "flag": "COST_NOT_FOUND",
                     "note": f"чака човек (канон) | {r['evidence']}{prod_note}"})
    boq.append(line)

total = round(sum(l["sumEur"] or 0 for l in boq), 2)
vat = round(total * 0.2, 2)
sections = {}
for l in boq:
    sections[l["section"]] = round(sections.get(l["section"], 0) + (l["sumEur"] or 0), 2)

pack = {"tenderId": 467490, "generatedAt": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "boq": boq,
        "pricing": {"totalExclVat": total, "vat": vat, "totalInclVat": round(total + vat, 2),
                    "capExclVat": 1746913.92, "currency": "EUR", "vatRate": 0.2,
                    "costVersion": "draft scope: Тех.спецификация 467490 (таблици) · цени: canonical costdb matched"}}

out = ROOT / "tenderexamples" / "T467490-Радомир_DRAFT_КСС.xlsx"
subm._write_kss(out, pack)

methods = Counter((l.get("note") or "").split(" · ")[1] if l["rule"] == "CSV" else "—" for l in boq)
print(f"rows={len(boq)} priced={n_csv} ({n_csv/len(boq)*100:.0f}%) метод={dict(methods)}")
print(f"Σ priced scope: {total:,.2f} € excl VAT (cap 1 746 913.92 €)")
print(f"draft -> {out}")
EST = [l for l in boq if l["rule"] == "EST"]
print(f"awaiting human: {len(EST)} rows (blank in the draft, by design)")
# coverage by section
cov = {}
for l in boq:
    a = cov.setdefault(l["section"], [0, 0])
    a[1] += 1
    if l["rule"] == "CSV":
        a[0] += 1
for s, (p, t) in cov.items():
    print(f"  {p:3}/{t:3}  {s}")
