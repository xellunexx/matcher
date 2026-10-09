import json, sys
sys.path.insert(0, ".")
from app import spatial_geometry as G, spatial_fusion as F, spatial_scene as S
from app import pipeline as P
base = r"C:\Users\ochak\Downloads\datiumremaikatabokluk\examples\two_houses_plot"
names = ("site_plan.dxf", "house_A_spec.pdf", "house_B_spec.pdf", "technical_spec.docx", "kss_two_houses.xlsx")
results = [G.extract_geometry(f"{base}\\{n}") for n in names]
ev = F.fuse(results)
print("frame:", ev["frame"])
for b in ev["buildings"]:
    print("--", b["id"], "tag", b["tag"], b["tag_state"], b["tag_basis"], b["footprint_locator"])
    print("   fp:", b["footprint"]["polygon_m"])
    print("   dims:", {k: (v["value"], v["state"], v["source"].split("\\")[-1]) if v else None for k, v in b["dimensions"].items()})
    print("   roof:", b["roof"])
print("site:", ev.get("site_boundary") and ev["site_boundary"].get("polygon_m"))
print("networks:", [(n["kind"], n["layer"], n["polyline_m"]) for n in (ev.get("networks") or [])])

from pathlib import Path
rows = P.parse_kss(Path(f"{base}\\kss_two_houses.xlsx"))
print("\nBOQ rows:", len(rows))
boq = []
for i, r in enumerate(rows):
    qty = r.get("qty") or 0
    boq.append({"key": f"R{i+1}", "section": r.get("section") or "", "sub": r.get("sub") or "",
                "desc": r.get("desc") or "", "unit": r.get("unit") or "", "qty": qty,
                "unitEur": 10.0, "sumEur": round(10.0 * float(qty), 2), "rule": "CSV"})
    print(f"  {boq[-1]['key']:4} [{boq[-1]['section'][:28]:28}] [{boq[-1]['sub'][:18]:18}] {boq[-1]['desc'][:60]} | {boq[-1]['unit']} {qty}")

sc = S.compile_scene(ev, {"boq": boq})
print("\nreadiness:", sc["readiness"], "|", sc["readiness_reason"])
print("bounds:", sc["bounds_m"])
print("buildings:", sc["buildings"])
print("frame:", sc["frame"])
ca = sc["cost_allocation"]
print("cost_allocation:", {k: v for k, v in ca.items() if k != "unallocated"})
print("unallocated:", ca["unallocated"])
print("\nobjects:", len(sc["objects"]))
for o in sc["objects"]:
    g = o["geometry"]
    gd = g["type"]
    if gd in ("extrude", "gable_roof"):
        xs = [p[0] for p in g["polygon_m"]]; ys = [p[1] for p in g["polygon_m"]]
        gd += f" x[{min(xs):.1f},{max(xs):.1f}] y[{min(ys):.1f},{max(ys):.1f}]"
        gd += f" z[{g.get('z0_m', g.get('eave_z_m'))},{g.get('z1_m', g.get('ridge_z_m'))}]"
        if g["type"] == "gable_roof":
            gd += f" axis={g['ridge_axis']}"
    elif gd == "box":
        gd += f" c={g['center_m']} s={g['size_m']}"
    elif gd == "tube":
        gd += f" n={len(g['points_m'])} r={g['radius_m']} z={g['points_m'][0][2]}"
    cost = o["inspect"].get("cost_eur")
    share = o["inspect"].get("cost_share")
    print(f"  {o['id']:28} {o['semantic_type']:14} {o['operation']:9} {o['knowledge_state']:9} {gd}")
    print(f"      label={o['label']!r} qty={o.get('quantity')} {o.get('unit') or ''} cost={cost} share={share}")
print("\nnotes:")
for n in sc["notes"]:
    print("  -", n)
json.dump(sc, open("_probe_scene.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
