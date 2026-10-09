import asyncio, sys, io, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ.setdefault("OE_CLI_DATA_DIR", r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp-data")

from sqlalchemy import select
from app.config import get_settings
from app.database import async_session_factory
from app.modules.costs.models import CostItem

async def main():
    s = get_settings()
    print("backend:", s.vector_backend, "| model:", s.embedding_model_name, "| dim:", s.embedding_model_dim, flush=True)
    print("db:", s.database_url[:60], flush=True)
    async with async_session_factory() as db:
        rows = list((await db.execute(
            select(CostItem).where(CostItem.is_active.is_(True))
        )).scalars().all())
    print("active cost rows:", len(rows), flush=True)

    if "--dry" not in sys.argv:
        from app.modules.costs.vector_adapter import reindex_all, collection_count
        t = time.time()
        res = await reindex_all(rows, batch_size=200)
        print("reindex:", res, "elapsed", round(time.time()-t,1), "s", flush=True)
        print("collection count:", await collection_count(), flush=True)

asyncio.run(main())
