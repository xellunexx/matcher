# -*- coding: utf-8 -*-
"""Forensics: do the DB resolutions actually line up with live blocker keys?"""
import json, sqlite3, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
con = sqlite3.connect(r"dist\tenderops.sqlite3")
rows = con.execute("SELECT tender_id,boq_key,substr(note,1,50),actor,at FROM human_resolutions ORDER BY id").fetchall()
print(f"total res rows: {len(rows)}")
for r in rows:
    print(" ", r)
con.close()

# what are the actual blocker keys in the live packs?
for pf in Path(r"dist\data\demo\processed").glob("*.json"):
    if ".error." in pf.name:
        continue
    pk = json.loads(pf.read_text(encoding="utf-8-sig"))
    keys = [l["key"] for l in pk.get("boq", []) if l.get("rule") == "EST" or l.get("flag")]
    print(pf.stem, "| blocker keys:", keys[:12], "(%d)" % len(keys))

# load-storm noise
print("LOAD-* noise count:", sum(1 for r in rows if str(r[1]).startswith("LOAD-")))
