# -*- coding: utf-8 -*-
"""Probe the Buildly scrape: shapes, distributions, our known gaps."""
import collections, json
from pathlib import Path

f = Path(r"C:\Users\ochak\Downloads\sek_scrape\tenderops_buildy_cost_items.json")
d = json.loads(f.read_text(encoding="utf-8-sig"))
print("items:", len(d))
print("types:", collections.Counter(str(r.get("type")) for r in d))
print("statuses:", collections.Counter(str(r.get("status")) for r in d))
print("vatIncluded:", collections.Counter(str((r.get("money") or {}).get("vatIncluded")) for r in d))
print("units top:", collections.Counter(str(r.get("unit")) for r in d).most_common(12))
cats = collections.Counter(str(r.get("categoryLabel")) for r in d)
print("categoryLabels:", len(cats), "top10:", cats.most_common(10))
amts = [(r.get("money") or {}).get("amount") for r in d]
bad = [a for a in amts if not isinstance(a, (int, float)) or a <= 0]
print("bad amounts:", len(bad), "| max:", max(amts), "| zeros:", sum(1 for a in amts if a == 0))
print("bigon > 50k:", sorted([a for a in amts if a > 50000], reverse=True)[:5])

# THE gap: playground stuff from 606060
needles = ["клатуш", "детск", "пейк", "беседк", "пясъчник", "люле"],
for n in ["клатуш", "детск", "пейк", "беседк", "пясъчник", "люле", "качул", "детска"]:
    hits = [r for r in d if n in (r.get("name") or "").lower()]
    print(f"{n}: {len(hits)}", "| sample:", (hits[0]["name"][:70] if hits else "-"),
          (hits[0]["money"]["amount"] if hits else ""))
# wonky confusables?
weird = sum(1 for r in d if " H" in (r.get("name") or "") or " HА" in (r.get("name") or ""))
print("latin-H-ish names:", weird)
ids = [r["id"] for r in d]
print("unique ids:", len(set(ids)))
