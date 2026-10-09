# -*- coding: utf-8 -*-
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from pathlib import Path
from app import spatial_geometry as sg, spatial_fusion as sf, spatial_scene as ss, pipeline as pl

EX = Path(r"C:\Users\ochak\Downloads\datiumremaikatabokluk\examples\two_houses_plot")
files = ["site_plan.dxf", "house_A_spec.pdf", "house_B_spec.pdf", "technical_spec.docx", "kss_two_houses.xlsx"]

rows = pl.parse_kss_priced(EX / "kss_two_houses.xlsx")
alt = pl.parse_kss(EX / "kss_two_houses.xlsx")
print("priced rows:", len(rows), " alt rows:", len(alt))
boq = []
for i, r in enumerate(rows):
    line = {"key": f"R{i+1}", "row": i + 1, "section": r["section"], "sub": r.get("sub") or "",
            "desc": r["desc"], "unit": r["unit"], "qty": r["qty"]}
    if r.get("own_price"):
        line.update({"rule": "CSV", "unitEur": r["own_price"], "sumEur": round(r["own_price"] * r["qty"], 2)})
    else:
        line.update({"rule": "EST", "unitEur": 0, "sumEur": 0})
    boq.append(line)
for l in boq[:8]:
    print("   ", l["key"], "|", l["section"][:30], "|", l["sub"][:30], "|", l["desc"][:45], "|", l["sumEur"])
print("   ...")
secs = sorted({l["section"] for l in boq}); print("sections:", secs)
subs = sorted({l["sub"] for l in boq}); print("subs:", subs[:30])

results = [sg.extract_geometry(str(EX / f)) for f in files if not f.endswith(".xlsx")]
ev = sf.fuse(results)
pack = {"boq": boq, "geometry_evidence": ev}
scene = ss.compile_scene(ev, pack, None)
print("=" * 80)
print("readiness:", scene["readiness"], scene["readiness_reason"])
print("bounds:", scene["bounds_m"])
print("notes:", scene["notes"])
tot_cost = 0
for o in scene["objects"]:
    g = o["geometry"]
    ext = ""
    if g["type"] in ("extrude", "gable_roof"):
        xs = [p[0] for p in g["polygon_m"]]; ys = [p[1] for p in g["polygon_m"]]
        ext = f"x[{min(xs):.1f}..{max(xs):.1f}] y[{min(ys):.1f}..{max(ys):.1f}]"
    elif g["type"] == "box":
        ext = f"c={g['center_m']} s={g['size_m']}"
    elif g["type"] == "tube":
        ext = f"pts={g['points_m'][0]}..{g['points_m'][-1]}"
    cost = o["inspect"].get("cost_eur")
    print(f"  {o['id']:24s} {o['semantic_type']:12s} {g['type']:10s} {o['knowledge_state']:9s} "
          f"keys={len(o['boq_keys']):2d} cost={cost!s:>10s} {ext}")
print("BOQ total:", round(sum(l["sumEur"] for l in boq), 2))
matched = {k for o in scene["objects"] for k in o["boq_keys"]}
print("rows matched to at least one object:", len(matched), "/", len(boq))
unmatched = [l for l in boq if l["key"] not in matched]
for l in unmatched:
    print("   UNMATCHED", l["key"], l["section"][:28], "|", l["desc"][:50])
json.dump(scene, open("_probe_scene.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
