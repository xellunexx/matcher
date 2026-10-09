import sys, io, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"C:\lab\tenderops\tenderops\app")
import costdb

info = costdb.sync_json_sources(
    r"C:\lab\tenderops\tenderops\data\demo",
    r"C:\lab\tenderops\tenderops\tenderops.sqlite3",
    force=True)
print("sync:", info if not isinstance(info, dict) else {k: v for k, v in info.items() if k != "sources"})
con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
con.row_factory = sqlite3.Row
print("total:", con.execute("SELECT COUNT(*) c FROM cost_items").fetchone()["c"])
for r in con.execute("SELECT status, COUNT(*) c FROM cost_items GROUP BY status"):
    print(" ", dict(r))
