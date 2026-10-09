# -*- coding: utf-8 -*-
"""How do tender list rows carry IDs in the locked UI?"""
import re

t = open("app/web/app.js", encoding="utf-8").read()
i = t.find("function renderTenderList")
seg = t[i:i + 2400]
print(seg[:2000])
print("---")
print("data- attrs count:", len(re.findall(r"data-[a-z-]+=", t)))
