import json, urllib.request, urllib.parse
BASE = 'http://127.0.0.1:7100/api/v1'
def req(m, p, b=None, t=None):
    r = urllib.request.Request(BASE + p, method=m)
    r.add_header('Content-Type', 'application/json')
    if t: r.add_header('Authorization', 'Bearer ' + t)
    return json.load(urllib.request.urlopen(r, json.dumps(b).encode() if b is not None else None))
tok = req('POST', '/users/auth/login/', {'email': 'xel@tenderops.io', 'password': 'xel12345'})['access_token']
for q in ['beton', 'concrete wall', 'мазилка']:
    r = req('GET', '/costs/vector/search/?' + urllib.parse.urlencode({'q': q, 'limit': 5}), t=tok)
    print('Q:', q, '->', type(r).__name__)
    print(json.dumps(r, ensure_ascii=False)[:1000])
    print()
