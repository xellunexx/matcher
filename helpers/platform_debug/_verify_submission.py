# -*- coding: utf-8 -*-
"""Live proof: submission.zip for 586395 through the rebuilt exe."""
import io, json, sys, urllib.request, zipfile
sys.stdout.reconfigure(encoding="utf-8")

r = urllib.request.urlopen("http://127.0.0.1:8077/api/tender/586395/submission.zip", timeout=600)
data = r.read()
print("HTTP", r.status, "| bytes:", len(data))
z = zipfile.ZipFile(io.BytesIO(data))
names = z.namelist()
print("zip contents:")
for n in names:
    print("  ", n)
man = json.loads(z.read([n for n in names if n.endswith("MANIFEST.json")][0]).decode("utf-8"))
print("files manifest:")
for f in man["files"]:
    print("  -", f["file"], "::", f.get("what"))
