# -*- coding: utf-8 -*-
"""Scan levelup renderBoq row markup for evidence hooks."""
import re

t = open("app/web/app.js", encoding="utf-8").read()
i = t.find("function renderBoq")
seg = t[i:i + 2600]
print("onclick handlers:", re.findall(r"onclick=\S[^ >]*", seg)[:6])
print("rule-tag classes:", re.findall(r"rule-tag[^\s\"']*", seg)[:8])
print("row builder snippet:")
r = re.search(r"rowsHtml|const draw", seg)
print(seg[r.start():r.start() + 1100] if r else "not found")
