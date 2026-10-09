# -*- coding: utf-8 -*-
import io, json, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
DEMO = Path(r"C:\Users\ochak\OneDrive\Documents\Default Project\tenderops\data\demo")
names = ["boq_601701.json", "pricing_601701.json", "rules_601701.json", "documents_601701.json", "clarifications_601701.json", "resolutions.json"]
for n in names:
    try:
        d = json.load(open(DEMO / n, encoding="utf-8"))
        s = json.dumps(d, ensure_ascii=False).encode("utf-8")
        print(n, "OK", len(s), "bytes")
    except Exception as e:
        print(n, "FAIL", repr(e))
