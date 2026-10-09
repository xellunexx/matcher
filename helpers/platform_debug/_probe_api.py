# -*- coding: utf-8 -*-
"""Probe v3: HTTP-level estimation scan -> run -> spatial chain diagnosis."""
import json, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8078"

def req(path, method="GET", data=None, ctype="application/octet-stream"):
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data and ctype: r.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(r, timeout=240) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode("utf-8-sig"))
        except Exception: return e.code, {"raw": str(e)}

payload = open("tenderexamples/KSS_ocenena_EST20260906_052957.xlsx", "rb").read()
st, scan = req("/api/estimation/scan?filename=KSS_ocenena_EST20260906_052957.xlsx", "POST", payload)
print("SCAN", st, json.dumps(scan, ensure_ascii=False)[:400])

st, run = req("/api/estimation/run", "POST", b"{}", "application/json")
print("RUN", st, json.dumps(run, ensure_ascii=False)[:600])
est = run.get("estimationId")
if est:
    st, sp = req(f"/api/estimation/spatial?id={est}")
    print("SPATIAL", st)
    if isinstance(sp, dict):
        print(" schema:", sp.get("schema"))
        proj = sp.get("project") or {}
        print(" project.spatializable:", proj.get("spatializable"), "| readiness:", proj.get("readiness"), "| repr:", proj.get("representation"))
        print(" scene:", json.dumps(sp.get("scene") or proj.get("scene"), ensure_ascii=False)[:200] if (sp.get("scene") or proj.get("scene")) else None)
        print(" elements:", len(sp.get("elements") or []), "| keys:", list(sp.keys())[:12])
        print(" err:", sp.get("error"))
