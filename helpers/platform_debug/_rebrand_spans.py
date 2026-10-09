"""Second pass: lines that mix the product name with protected spans.

Protects URL/command/email spans in-place (placeholder tokens), replaces the
remaining PascalCase brand literal, then restores the spans. Skips comment
lines exactly like pass 1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp")
SRC = ROOT / "frontend" / "src"

OLD = "OpenConstructionERP"
NEW = "TenderOps ERP"

# Regex-level protected spans (applied before replacement).
PROTECTED_RES = [
    re.compile(r"https?://[^\s\"'<>]*"),
    re.compile(r"pip install[^\n\"']*"),
    re.compile(r"python -m [^\n\"']*"),
    re.compile(r"[\w.+-]+@openconstructionerp\.com"),
    re.compile(r"openconstructionerp\.com"),
    re.compile(r"OpenConstructionEstimate[^\s\"']*"),
    re.compile(r"openconstructionerp-log-"),  # handled separately below
]

COMMENT_PREFIXES = ("//", "/*", "*", "{/*")


def process(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    if OLD not in text:
        return 0

    out: list[str] = []
    count = 0
    for line in text.split("\n"):
        if OLD not in line:
            out.append(line)
            continue
        if line.lstrip().startswith(COMMENT_PREFIXES):
            out.append(line)
            continue

        spans: list[str] = []
        def _stash(m: re.Match) -> str:
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"

        work = line
        for rx in PROTECTED_RES:
            work = rx.sub(_stash, work)

        n = work.count(OLD)
        if n:
            work = work.replace(OLD, NEW)
            count += n
        for i, s in enumerate(spans):
            work = work.replace(f"\x00{i}\x00", s)
        out.append(work)

    if count:
        path.write_text("\n".join(out), encoding="utf-8")
    return count


def main() -> int:
    total = 0
    for path in sorted(SRC.rglob("*")):
        if path.suffix.lower() not in {".ts", ".tsx", ".json", ".html"} or not path.is_file():
            continue
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        n = process(path)
        if n:
            print(f"{path.relative_to(SRC)}: {n}")
            total += n
    print(f"\nTOTAL: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
