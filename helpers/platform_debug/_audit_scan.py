# -*- coding: utf-8 -*-
"""Forensic reads for the §32 audit (no patching)."""
import json, re

t = open("app/web/app.js", encoding="utf-8").read()
print("=== safeStore uses:")
for l in t.splitlines():
    if "safeStore" in l:
        print("  ", l.strip()[:140])
print()
print("=== boot flow:")
i = t.find("function boot")
print(t[i:i + 700] if i >= 0 else "no boot fn")
i = t.find("function setTender")
print(t[i:i + 400] if i >= 0 else "absent")

r = json.load(open("data/demo/registry.json", encoding="utf-8-sig"))
print("\nregistry tenders:", len(r["tenders"]))
for x in r["tenders"]:
    print("  ", x.get("id"), (x.get("name") or "")[:56])

s = json.load(open("data/demo/settings.json", encoding="utf-8-sig"))
print("\nsettings: llm.base=", s.get("llm", {}).get("base_url"), "| model=", s.get("llm", {}).get("model"))
print("firm:", s.get("firm") is not None)
