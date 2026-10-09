# -*- coding: utf-8 -*-
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

file_path = Path("data/_inve_prices.xlsx")
raw = file_path.read_bytes()

url = (
    "http://127.0.0.1:8077/api/tender/605862/"
    "upload_prices?filename=_inve_prices.xlsx"
)

req = urllib.request.Request(
    url,
    data=raw,
    headers={
        "Content-Type": "application/octet-stream"
    },
    method="POST",
)

try:
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8")
        print("STATUS:", r.status)
        print("RESPONSE:")
        print(body)

except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print("STATUS:", e.code)
    print("RESPONSE:")
    print(body)