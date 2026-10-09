# -*- coding: utf-8 -*-
"""Live Gate-1 verification against the running exe."""
import json, sqlite3, urllib.request
import sys
sys.stdout.reconfigure(encoding="utf-8")

def post(path, payload, t=900):
    b = json.dumps(payload).encode()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8077" + path, data=b,
                               headers={"Content-Type": "application/json"}), timeout=t)
    return json.loads(r.read())

print("resolve:", post("/api/resolve", {"key": "R-CHk", "note": "проверка на гейт 1"})["ok"])
con = sqlite3.connect(r"dist\tenderops.sqlite3")
print("human_resolutions rows:", con.execute("SELECT COUNT(*) FROM human_resolutions").fetchone()[0])
print("env versions:", con.execute("SELECT id FROM cost_env_versions").fetchall())
print("tables:", sorted(x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")))
con.close()

r2 = post("/api/tender/605862/process", {})
print("process ok:", r2.get("ok"))
pk = json.load(open(r"dist\data\demo\processed\605862.json", encoding="utf-8-sig"))
print("pack costEnvVersion:", pk["pricing"].get("costEnvVersion"))
con = sqlite3.connect(r"dist\tenderops.sqlite3")
print("run env pin:", con.execute("SELECT cost_env_id FROM runs ORDER BY id DESC LIMIT 1").fetchone())
print("run states tail:", con.execute("SELECT state FROM run_events ORDER BY id DESC LIMIT 4").fetchall())
con.close()
