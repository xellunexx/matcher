# Clean slate: wipe operational data (projects, BOQs, match history, files,
# AI runs) from the ERP DB; corpus tables (oe_costs_*, oe_cost_item_resource)
# and users are preserved. Then wipe tenderops SQLite operational tables.
#
# DSN is discovered from the embedded cluster's postmaster.opts - the port
# is ephemeral and changes on every restart, never hardcode it.
import os, re, sys, asyncio
from pathlib import Path

PGDATA = Path(r"C:\lab\tenderops\platform\erp-data\pgdata")
opts = (PGDATA / "postmaster.opts").read_text(encoding="utf-8", errors="replace")
port = re.search(r'"-p"\s+"(\d+)"', opts).group(1)
m = re.search(r'"-h"\s+"([^"]+)"', opts)
host = m.group(1) if m else "127.0.0.1"
# The embedded cluster keeps everything in the `postgres` database.
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://postgres@{host}:{port}/postgres"
print(f"ERP DSN: postgres@{host}:{port}/postgres")

sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
sys.stdout.reconfigure(encoding="utf-8")
import sqlite3
from sqlalchemy import text
from app.database import async_session_factory

WIPE_PREFIXES = (
    "oe_boq_", "oe_projects_", "oe_project_", "oe_documents_",
    "oe_file_", "oe_cost_match_", "oe_ai_", "oe_estimate_",
    "oe_tendering_", "oe_costmodel_", "oe_variations_",
    "oe_funding_", "oe_accommodation_", "oe_project_route_",
    "oe_spatial_", "oe_takeoff_", "oe_pricing_",
)
WIPE_EXACT = {"oe_activity_log", "oe_activity", "oe_notifications_notification"}
KEEP_EXACT = {
    "oe_costs_catalog", "oe_costs_item", "oe_cost_item_resource",
    "oe_cost_item_usage", "oe_cost_recovery_apportionment",
    "oe_cost_recovery_back_charge", "oe_labor_rates_oncost",
}


async def main():
    async with async_session_factory() as s:
        tabs = [r[0] for r in (await s.execute(text(
            "select table_name from information_schema.tables "
            "where table_schema='public' and table_name like 'oe%'"))).fetchall()]
        wipe = [t for t in tabs
                if (t in WIPE_EXACT or t.startswith(WIPE_PREFIXES))
                and t not in KEEP_EXACT]
        keep = sorted(set(tabs) - set(wipe))
        print(f"tables: {len(tabs)} | wipe: {len(wipe)} | keep: {len(keep)}")
        print("keeping:", ", ".join(keep))
        if not wipe:
            print("nothing to wipe")
            return
        counts = {}
        for t in wipe:
            try:
                counts[t] = await s.scalar(text(f"select count(*) from {t}"))
            except Exception:
                counts[t] = "?"
        await s.rollback()
        stmt = "truncate " + ", ".join(sorted(wipe)) + " cascade"
        await s.execute(text(stmt))
        await s.commit()
        print("\nwiped (row counts):")
        for t, n in sorted(counts.items()):
            if n:
                print(f"  {t}: {n}")
        # sanity: corpus untouched
        n = await s.scalar(text("select count(*) from oe_costs_item"))
        a = await s.scalar(text(
            "select count(*) from oe_costs_item where metadata ? 'bill_terms'"))
        print(f"\npost-wipe: oe_costs_item={n}, bill_terms rows={a}")

asyncio.run(main())

# ---- tenderops SQLite ----
print("\n=== tenderops sqlite ===")
db = r"C:\lab\tenderops\platform\tenderops\tenderops.sqlite3"
con = sqlite3.connect(db)
con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
tables = [r[0] for r in con.execute(
    "select name from sqlite_master where type='table'")]
wipe = [t for t in tables if t in (
    "runs", "run_events", "matches", "match_candidates",
    "human_resolutions", "outcomes", "investor_projects",
    "project_graphs", "tender_fingerprints", "audit_events",
    "telemetry_events", "telemetry_incidents", "telemetry_sessions")]
keep = [t for t in tables if t not in wipe]
print("keep:", ", ".join(sorted(keep)))
for t in wipe:
    n = con.execute(f"select count(*) from {t}").fetchone()[0]
    con.execute(f"delete from {t}")
    print(f"  wiped {t}: {n}")
con.commit()
con.execute("vacuum")
con.close()
print("tenderops sqlite cleaned")
