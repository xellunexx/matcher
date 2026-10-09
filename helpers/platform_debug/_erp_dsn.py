# Resolve the live ERP PostgreSQL DSN. The embedded cluster's port is
# EPHEMERAL - it changes on every restart and is only knowable from
# postmaster.opts inside the data dir. Everything lives in the `postgres`
# database; there is no separate "openconstructionerp" dbname.
#
# Usage:
#   python _erp_dsn.py          -> prints "host port dbname user"
#   python _erp_dsn.py url      -> prints a SQLAlchemy asyncpg URL
import re, sys
from pathlib import Path

PGDATA = Path(r"C:\lab\tenderops\platform\erp-data\pgdata")
opts = (PGDATA / "postmaster.opts").read_text(encoding="utf-8", errors="replace")
port = re.search(r'"-p"\s+"(\d+)"', opts).group(1)
m = re.search(r'"-h"\s+"([^"]+)"', opts)
host = m.group(1) if m else "127.0.0.1"
dbname, user = "postgres", "postgres"

if len(sys.argv) > 1 and sys.argv[1] == "url":
    print(f"postgresql+asyncpg://{user}@{host}:{port}/{dbname}")
else:
    print(host, port, dbname, user)
