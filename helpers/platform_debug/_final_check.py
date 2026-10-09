import json
import urllib.request

login = urllib.request.Request(
    "http://127.0.0.1:7100/api/v1/users/auth/login/",
    data=json.dumps({"email": "xel@tenderops.io", "password": "xel12345"}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
token = json.loads(urllib.request.urlopen(login).read())["access_token"]

def api(path):
    r = urllib.request.Request("http://127.0.0.1:7100" + path,
                               headers={"Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(r).read())

cats = api("/api/v1/costs/catalogs/")
print("catalogs:", len(cats), "| active items:", sum(c.get("item_count", 0) for c in cats))
import urllib.parse
for term in ("webanchors-66-302658731ebc2381", "SEK-", "ДЪСКИ", "BUILDLY-MATERIAL"):
    ac = api("/api/v1/costs/autocomplete/?q=" + urllib.parse.quote(term) + "&limit=5")
    print(f"autocomplete {term!r}:", [(x.get("code"), x.get("rate"), x.get("currency")) for x in ac][:5])
sr = api("/api/v1/costs/?limit=3&q=" + urllib.parse.quote("BETON"))
items = sr.get("items", sr) if isinstance(sr, dict) else sr
print("list 'BETON':", [(x.get("code"), x.get("description", "")[:40]) for x in items][:3])
sr = api("/api/v1/costs/?limit=3&q=" + urllib.parse.quote("ДЪСКИ"))
items = sr.get("items", sr) if isinstance(sr, dict) else sr
print("list 'ДЪСКИ':", [(x.get("code"), x.get("description", "")[:40]) for x in items][:3])
