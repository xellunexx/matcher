# -*- coding: utf-8 -*-
"""Find where `t` is shadowed as a local var, breaking i18n.t()."""
import re

t = open("app/web/app.js", encoding="utf-8").read()
for pat in ["tenderStatus", "renderTenders", "backToTenders"]:
    i = t.find(pat)
    print("===", pat, "at", i)
    print(t[i:i + 900] if i >= 0 else "absent")
    print()
