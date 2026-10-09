"""Dump curated bill_terms from the live ERP corpus -> app/corpus_aliases.py table."""
import asyncio, json, sys
sys.stdout.reconfigure(encoding="utf-8")
import asyncpg

async def main():
    c = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:60476/postgres")
    rows = await c.fetch(
        "SELECT code, metadata->'bill_terms' bt FROM oe_costs_item "
        "WHERE metadata ? 'bill_terms' AND jsonb_array_length(metadata->'bill_terms')>0 "
        "ORDER BY code")
    out = {}
    for r in rows:
        terms = json.loads(r["bt"])
        out[r["code"]] = terms
    print(f"rows: {len(out)}, terms: {sum(len(v) for v in out.values())}")
    path = r"C:\lab\tenderops\platform\_bill_terms_dump.json"
    open(path, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    print("dump ->", path)
    await c.close()

asyncio.run(main())
