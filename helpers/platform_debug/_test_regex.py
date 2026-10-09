import os, sys, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
from sqlalchemy import text
from app.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        tests = [
            (r"\yск\y", "кран ск ф2"),
            (r"(^|[^а-яa-z0-9_])ск([^а-яa-z0-9_]|$)", "кран ск ф2"),
            (r"(^|[^а-яa-z0-9_])ск([^а-яa-z0-9_]|$)", "кран скоба ф2"),
            (r"(^|[^а-яa-z0-9_])ф\s*2([^а-яa-z0-9_]|$)", "кран ск ф2"),
            (r"(^|[^а-яa-z0-9_])ф\s*2([^а-яa-z0-9_]|$)", "кран ф 20"),
            (r"\yск\y", "кран SK ф2"),
            (r"^.*show_lc$", "x"),
        ]
        for pat, sample in tests:
            r = (await s.execute(text("select :sample ~* :pat"), {"sample": sample, "pat": pat})).scalar()
            print(f"{pat!r} on {sample!r} -> {r}")
        lc = (await s.execute(text("show lc_ctype"))).scalar()
        print("lc_ctype:", lc)


asyncio.run(main())
