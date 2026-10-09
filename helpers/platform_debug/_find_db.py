import asyncio, asyncpg, sys
sys.stdout.reconfigure(encoding="utf-8")

async def m():
    c = await asyncpg.connect(host="127.0.0.1", port=49188,
                              user="postgres", password="postgres",
                              database="postgres", timeout=5)
    t = await c.fetch(
        "select table_name from information_schema.tables "
        "where table_name like '%cost%' order by 1")
    print("cost tables:", [r[0] for r in t])
    dbs = await c.fetch("select datname from pg_database where not datistemplate")
    print("dbs:", [r[0] for r in dbs])
    for r in t:
        try:
            n = await c.fetchval(f"select count(*) from {r[0]}")
            print(" ", r[0], n)
        except Exception as e:
            print(" ", r[0], "ERR", str(e)[:80])
    await c.close()

asyncio.run(m())
