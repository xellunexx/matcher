import sys, io, asyncio, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.chdir(r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
sys.path.insert(0, r"C:\lab\tenderops\zzzzzen_backup_20261003_0600\erp\backend")
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres@127.0.0.1:56729/postgres"
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

BOQ = "'3824c126-e054-4b65-aa51-16ddc493d821'"
RATE = "nullif(unit_rate,'')::numeric"
QTY = "nullif(quantity,'')::numeric"

async def main():
    eng = create_async_engine(os.environ["DATABASE_URL"])
    async with eng.connect() as c:
        print("-- price_basis on positions --")
        for r in (await c.execute(text(
            "select coalesce(price_basis,'<none>') b, count(*) n, "
            "sum(coalesce(" + RATE + ",0)*coalesce(" + QTY + ",0)) amt "
            "from oe_boq_position where boq_id=" + BOQ +
            " and coalesce(node_type,'item') not in ('section','header') "
            "group by 1 order by n desc"
        ))):
            print(f"{r.n:6} {str(round(float(r.amt or 0),2)):>14}  {r.b}")
        t = (await c.execute(text(
            "select sum(coalesce(" + RATE + ",0)*coalesce(" + QTY + ",0)) from oe_boq_position where boq_id=" + BOQ
        ))).scalar()
        print("\ngrand total:", round(float(t or 0), 2))
        z = (await c.execute(text(
            "select count(*) from oe_boq_position where boq_id=" + BOQ +
            " and coalesce(node_type,'item')='item' and (" + RATE + " is null or " + RATE + "=0) and " + QTY + ">0"
        ))).scalar()
        print("zero-priced item rows:", z)
        print("\n-- key rows --")
        for r in (await c.execute(text(
            "select ordinal p, left(description,55) d, unit, unit_rate, price_basis, "
            "confidence, left(coalesce(metadata->'cost_match'->>'suggested_code',''),30) ref "
            "from oe_boq_position where boq_id=" + BOQ + " and ("
            "description ilike '%подложка%' or description ilike '%Разрушав%' "
            "or description ilike '%тръба ф%' or description ilike '%демонтаж%') "
            "order by ordinal limit 30"
        ))):
            print(f"{str(r.p):8} {str(r.unit):7} {str(r.unit_rate):>10} {str(r.price_basis):22} {str(r.confidence):5} {str(r.ref):30} {r.d}")
        print("\n-- top 10 amounts --")
        for r in (await c.execute(text(
            "select ordinal p, left(description,52) d, unit, unit_rate, " + QTY + " q, "
            "coalesce(" + RATE + ",0)*coalesce(" + QTY + ",0) amt, price_basis "
            "from oe_boq_position where boq_id=" + BOQ +
            " and " + RATE + " is not null order by 6 desc limit 10"
        ))):
            print(f"{str(r.p):8} {str(r.unit):7} {str(r.unit_rate):>10} x{str(r.q):>8} = {round(float(r.amt),2):>12}  {str(r.price_basis):20} {r.d}")
    await eng.dispose()

asyncio.run(main())
