# -*- coding: utf-8 -*-
"""Scan the locked investor UI: endpoints, key flows, function atlas."""
import re

P = r"app\maybe\tenderops-levelup\tenderops-levelup\tenderops-investor-ui-locked\app.js"
t = open(P, encoding="utf-8").read()
print("lines:", len(t.splitlines()))
print("=== api endpoints:")
for m in sorted(set(re.findall(r"['\"`](/api/[a-zA-Z0-9_.${}/]+)['\"`]", t))):
    print("  ", m)
print("=== function counts:", len(re.findall(r"^(?:async )?(?:function|const \w+ = (?:async )?\()", t, re.M)))
for term in ["processOverlay", "processStage", "processProgress", "processPrompt", "review", "confirm", "history",
             "pack.spatial", "tenderUrl", "upload", "parser", "submission", "winner", "awarded", "compare",
             "gate", "human"]:
    hits = len(re.findall(re.escape(term), t, re.I))
    print(f"{term:16}: {hits}")
