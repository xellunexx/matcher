# -*- coding: utf-8 -*-
"""Scan the level-up package: endpoints used, key calls, surface check."""
import json, re

P = r"app\maybe\tenderops-levelup\tenderops-levelup\app.js"
t = open(P, encoding="utf-8").read()
print("app.js lines:", len(t.splitlines()))
print("=== /api/* referenced:")
for m in sorted(set(re.findall(r"['\"](/api/[a-zA-Z0-9_.${}/]+)['\"]", t))):
    print("  ", m)
print("=== fetch( count:", len(re.findall(r"\bfetch\(", t)))
print("=== ws/eventSource:", bool(re.search(r"WebSocket|EventSource", t)))
print("=== functions:", len(re.findall(r"^(?:async )?function ", t, re.M)))
print("=== named fns (first 30):")
for m in re.findall(r"^(?:async )?function ([a-zA-Z_$][\w$]*)", t, re.M)[:30]:
    print("  ", m)
c = open(r"app\maybe\tenderops-levelup\tenderops-levelup\app.css", encoding="utf-8").read()
print("app.css lines:", len(c.splitlines()), "| vars:", len(re.findall(r"--[\w-]+:", c)))
