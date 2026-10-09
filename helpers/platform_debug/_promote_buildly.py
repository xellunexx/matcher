import io, re, sys, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DEMO = r"C:\lab\tenderops\tenderops\data\demo"
files = ["costdb_seed_buildly_smr.json", "costdb_seed_buildly_materials.json",
         "costdb_seed_buildly_labor.json", "costdb_seed_buildly_machinery.json"]
pat = re.compile(r'("status"\s*:\s*)"pending_review"')
for fn in files:
    p = DEMO + "\\" + fn
    t = io.open(p, encoding="utf-8-sig").read()
    n = len(pat.findall(t))
    t2 = pat.sub(r'\1"active"', t)
    io.open(p, "w", encoding="utf-8", newline="").write(t2)
    print(f"{fn}: {n} rows -> active")

sys.path.insert(0, r"C:\lab\tenderops\tenderops\app")
import costdb
info = costdb.sync_json_sources(DEMO, r"C:\lab\tenderops\tenderops\tenderops.sqlite3", force=False)
print("sync:", {k: v for k, v in info.items() if k != "sources"})
con = sqlite3.connect(r"C:\lab\tenderops\tenderops\tenderops.sqlite3")
for r in con.execute("SELECT status, COUNT(*) c FROM cost_items GROUP BY status"):
    print(" ", r)
