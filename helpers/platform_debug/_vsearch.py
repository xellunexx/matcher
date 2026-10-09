import json, urllib.request, sys

BASE = "http://127.0.0.1:7100/api/v1"

def req(method, path, body=None, tok=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    data = json.dumps(body).encode() if body is not None else None
    return json.load(urllib.request.urlopen(r, data))

tok = req("POST", "/users/auth/login/",
          {"email": "xel@tenderops.io", "password": "xel12345"})["access_token"]

queries = [
    "Разрушаване на сгради и басейни с багер-чук - бетонови плочи, греди, стени, колони, основи",
    "Тухлена зидария от тухли Poroterm с d = 25см",
    "Вътрешна мазилка по стени",
    "Натоварване строителни отпадъци и транспорт на депо",
]
for q in queries:
    try:
        r = req("POST", "/costs/vector/search/", {"query": q, "limit": 5}, tok)
        print("Q:", q[:70])
        items = r if isinstance(r, list) else r.get("items", r.get("results", []))
        for it in items[:5]:
            print("   ", it.get("code"), "|", (it.get("description") or "")[:65],
                  "|", it.get("rate"), "| score:", it.get("score"))
        print()
    except Exception as e:
        print("Q:", q[:60], "-> ERR", e)
