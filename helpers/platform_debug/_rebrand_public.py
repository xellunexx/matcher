"""Span-level rebrand for the public/ user-facing pages."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OLD = "OpenConstructionERP"
NEW = "TenderOps ERP"

PROTECTED = [
    re.compile(r"https?://[^\s\"'<>]*"),
    re.compile(r"pip install[^\n\"']*"),
    re.compile(r"[\w.+-]+@openconstructionerp\.com"),
    re.compile(r"openconstructionerp\.com"),
    re.compile(r"OpenConstructionEstimate[^\s\"']*"),
]

PUB = Path(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\frontend\public")

for name in ["privacy-policy.html", "terms.html", "splash.html", "favicon.svg"]:
    p = PUB / name
    text = p.read_text(encoding="utf-8")
    out: list[str] = []
    count = 0
    for line in text.split("\n"):
        if OLD not in line:
            out.append(line)
            continue
        spans: list[str] = []

        def stash(m: re.Match) -> str:
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"

        w = line
        for rx in PROTECTED:
            w = rx.sub(stash, w)
        n = w.count(OLD)
        if n:
            w = w.replace(OLD, NEW)
            count += n
        for i, s in enumerate(spans):
            w = w.replace(f"\x00{i}\x00", s)
        out.append(w)
    if count:
        p.write_text("\n".join(out), encoding="utf-8")
    print(name, count)
