"""UI-scope rebrand: OpenConstructionERP -> TenderOps ERP.

Scope = text a user can see: frontend components + every locale file +
index.html metadata + rendered-document strings (PDF/XLSX/CSV/GAEB stamps)
in an explicit backend file list.

Protected (never touched):
- URLs: github.com/datadrivenconstruction/OpenConstructionERP,
  openconstructionerp.com, pypi.org
- e-mail addresses @openconstructionerp.com (seed/demo identities)
- package/CLI identifiers (lowercase `openconstructionerp` never matches
  the PascalCase literal anyway)
- comment lines (//, /*, *, #) and copyright headers - not UI
- upstream provenance markers (DDC-CWICR-OE watermark, /api/source)
- repo/data-source names: OpenConstructionEstimate-DDC-CWICR
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp")

FRONTEND_DIRS = [
    ROOT / "frontend" / "src",
]
FRONTEND_FILES = [
    ROOT / "frontend" / "index.html",
]
FRONTEND_EXTS = {".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".md"}

BACKEND_FILES = [
    ROOT / "backend" / "app" / "modules" / "reporting" / "report_translations.py",
    ROOT / "backend" / "app" / "modules" / "boq" / "router.py",
    ROOT / "backend" / "app" / "modules" / "boq" / "excel_io.py",
    ROOT / "backend" / "app" / "modules" / "bim_hub" / "exporters" / "boq_xlsx.py",
    ROOT / "backend" / "app" / "modules" / "bim_hub" / "exporters" / "cobie.py",
    ROOT / "backend" / "app" / "modules" / "bim_requirements" / "exporters" / "excel_exporter.py",
    ROOT / "backend" / "app" / "modules" / "bim_requirements" / "exporters" / "ids_exporter.py",
    ROOT / "backend" / "app" / "modules" / "design_options" / "exporters.py",
    ROOT / "backend" / "app" / "modules" / "file_transmittals" / "service.py",
    ROOT / "backend" / "app" / "modules" / "methodology" / "service.py",
    ROOT / "backend" / "app" / "modules" / "property_dev" / "service.py",
    ROOT / "backend" / "app" / "modules" / "file_manager_schemas.py",
    ROOT / "backend" / "app" / "modules" / "file_manager" / "schemas.py",
]

# Substrings that make a line untouchable (functional identifiers).
PROTECTED = (
    "github.com/datadrivenconstruction/OpenConstructionERP",
    "openconstructionerp.com",
    "pypi.org/pypi/openconstructionerp",
    "@openconstructionerp.com",
    "openconstructionerp[",
    "pip install openconstructionerp",
    "python -m openconstructionerp",
    "OpenConstructionEstimate",           # upstream data repo name
    '"iss": "openconstructionerp"',
    "openconstructionerp/cwicr",
    "openconstructionerp/service",
    "openconstructionerp-data",
)

COMMENT_PREFIXES = ("//", "/*", "*", "{/*", "#")

OLD = "OpenConstructionERP"
NEW = "TenderOps ERP"


def process(path: Path, *, skip_comments: bool) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    if OLD not in text:
        return 0

    out_lines: list[str] = []
    count = 0
    for line in text.split("\n"):
        if OLD not in line:
            out_lines.append(line)
            continue
        if any(p in line for p in PROTECTED):
            out_lines.append(line)
            continue
        stripped = line.lstrip()
        if skip_comments and stripped.startswith(COMMENT_PREFIXES):
            out_lines.append(line)
            continue
        n = line.count(OLD)
        out_lines.append(line.replace(OLD, NEW))
        count += n

    if count:
        path.write_text("\n".join(out_lines), encoding="utf-8")
    return count


def main() -> int:
    total = 0

    for d in FRONTEND_DIRS:
        for path in sorted(d.rglob("*")):
            if path.suffix.lower() not in FRONTEND_EXTS or not path.is_file():
                continue
            if "node_modules" in path.parts or "dist" in path.parts:
                continue
            n = process(path, skip_comments=True)
            if n:
                print(f"frontend {path.relative_to(ROOT)}: {n}")
                total += n

    for path in FRONTEND_FILES:
        if path.exists():
            n = process(path, skip_comments=False)
            if n:
                print(f"frontend {path.relative_to(ROOT)}: {n}")
                total += n

    for path in BACKEND_FILES:
        if not path.exists():
            print(f"MISSING {path.relative_to(ROOT)}")
            continue
        n = process(path, skip_comments=True)
        if n:
            print(f"backend {path.relative_to(ROOT)}: {n}")
            total += n

    print(f"\nTOTAL replacements: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
