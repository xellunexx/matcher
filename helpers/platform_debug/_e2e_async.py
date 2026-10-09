import sys, io, asyncio, os, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:53642/postgres")
import urllib.request
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from jose import jwt

BOQ = "9038d626-9261-47bc-bc82-e42a6ff2847f"
BASE = "http://127.0.0.1:7100/api/v1/cost-match"

async def get_user():
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.connect() as c:
        cols = [r[0] for r in (await c.execute(text(
            "select column_name from information_schema.columns where table_name='oe_users_user'"
        )))]
        want = [c for c in ("id", "email", "role", "tenant_id") if c in cols]
        r = (await c.execute(text(f"select {','.join(want)} from oe_users_user where is_active order by created_at limit 5"))).all()
        print("users:", [dict(x._mapping) for x in r])
        return dict(r[0]._mapping) if r else None

def req(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode()[:300]

async def main():
    from app.config import get_settings
    s = get_settings()
    secret = open(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\.shared_jwt_secret").read().strip() or s.jwt_secret
    u = await get_user()
    if not u:
        print("no users"); return
    tok = jwt.encode({"sub": str(u["id"]), "type": "access", "iat": int(time.time()),
                      "exp": int(time.time()) + 3600}, secret, algorithm=s.jwt_algorithm)

    code, body = req("POST", f"/boq/{BOQ}/run-async", tok, {})
    print("POST run-async ->", code, body if isinstance(body, str) else {k: body[k] for k in ("job_id","status")})
    if code not in (200, 201, 202):
        return
    job_id = body["job_id"]

    # poll
    for i in range(400):
        await asyncio.sleep(3)
        code, st = req("GET", f"/boq/{BOQ}/run-jobs/{job_id}", tok)
        if code != 200:
            print("poll ->", code, st); continue
        if st["status"] != "running":
            print("FINAL:", st["status"], st.get("error"))
            if st.get("result"):
                r = st["result"]
                print("lines:", r["lines"], "priced:", r["positions_priced"], "unpriced:", r["positions_unpriced"])
            break
        if i % 20 == 0:
            print(f"  ...running t+{i*3}s")
    else:
        print("still running at 20min cap")

asyncio.run(main())
