import urllib.request, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
B = "http://127.0.0.1:7100"

def call(method, path, data=None, tok=None):
    req = urllib.request.Request(B + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})},
        method=method)
    r = urllib.request.urlopen(req, timeout=60)
    body = r.read().decode()
    return r.status, json.loads(body) if body else {}

# login — users router is mounted under /api/v1/users
tok = None
for path, creds in [
    ("/api/v1/users/auth/login", {"email": "admin@openestimate.local", "password": "admin"}),
    ("/api/v1/users/auth/demo-login", {"email": "admin@openestimate.local"}),
    ("/api/v1/users/auth/demo-login", {}),
]:
    try:
        st, j = call("POST", path, creds)
        tok = j.get("access_token"); print("login ok:", path); break
    except Exception as e:
        print("login fail:", path, str(e)[:90])
if not tok:
    sys.exit(1)

st, projects = call("GET", "/api/v1/projects?limit=50", tok=tok)
items = projects if isinstance(projects, list) else projects.get("items") or projects.get("projects") or []
for p in items[-6:]:
    print("proj:", p.get("id"), "|", p.get("name"), "|", p.get("classification_standard"))
