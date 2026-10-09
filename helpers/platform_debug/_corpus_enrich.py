# -*- coding: utf-8 -*-
"""Corpus enrichment backfill: scope + search_text + spec into metadata JSONB.

One pass over oe_costs_item:
  metadata.scope       - material|labor|machine|service|composite (price kind)
  metadata.search_text - normalized alias surface (verbs stripped, x/h unified)
  metadata.extra.spec  - structured spec classes (Fi/PN/DN/C-class/mm/kW)

Then creates the expression index _price_spread's query needs.
"""
import sys, io, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'C:\lab\tenderops\platform\erp\backend')

import psycopg2
import psycopg2.extras

DSN = "host=127.0.0.1 port=49188 dbname=postgres user=postgres password=postgres"

from app.modules.cost_match.matcher import extract_specs  # noqa: E402

_VERB_STRIP = re.compile(
    r"^(доставка и монтаж|доставка|доставяне|монтаж|монтиране|демонтаж|"
    r"полагане|поставяне|вграждане|изграждане|изпълнение|направа|изработване|"
    r"ремонт|облицовка|мазилка|шпакловка|къртене|иззиждане|разваляне|"
    r"пробиване|шлайфане|обучение|почистване|изнасяне|транспортиране|смр)\b"
    r"\s*(на|със)?\s*",
    re.U,
)
_UNIT_ECHO = re.compile(
    r"[\s,;]*\(\s*(м2|м3|м|бр\.?|кг|т|ч\.?ч\.?|компл\.?|л|млн|брой)\s*\)\s*$", re.U
)


def search_text(desc: str) -> str:
    t = (desc or "").lower().strip()
    for _ in range(4):
        t2 = _VERB_STRIP.sub("", t).strip()
        if t2 == t:
            break
        t = t2
    t = _UNIT_ECHO.sub("", t)
    t = re.sub(r"(?<=\d)[xх](?=\d)", "x", t)
    return re.sub(r"\s+", " ", t).strip()


def derive_scope(source: str, unit: str, desc: str, meta: dict) -> str:
    u = (unit or "").lower()
    d = (desc or "").lower()
    tsk = (meta or {}).get("tenderops_source_key") or ""
    if source == "labor_pricelist" or re.search(r"(ч\.?ч|час|hour)", u):
        return "labor"
    if re.search(r"(маш|смяна|смен|\bден\b|\bday\b)", u):
        return "machine"
    if tsk == "buildly_smr":
        return "composite"
    if tsk == "buildly_materials":
        return "material"
    if re.search(r"(обучение|услуга|консултация|транспорт|проектиране|надзор)", d):
        return "service"
    if re.search(
        r"(монтаж|полагане|демонтаж|изграждане|изпълнение|направа|облицовка|"
        r"мазилка|шпакловка|зидария|бетон|смр|ремонт)",
        d,
    ):
        return "composite"
    return "material"


def main():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute(
        "SELECT id, source, unit, description, metadata FROM oe_costs_item"
    )
    rows = cur.fetchall()
    print(f"rows: {len(rows)}")

    enrich = []
    spec_filled = 0
    for rid, source, unit, desc, meta in rows:
        meta = meta or {}
        scope = derive_scope(source, unit, desc, meta)
        stext = search_text(desc)
        spec = extract_specs(desc)
        spec_j = {k: sorted(v) for k, v in spec.items()} if spec else None
        if spec_j:
            spec_filled += 1
        enrich.append((str(rid), scope, stext, json.dumps(spec_j) if spec_j else None))

    cur.execute(
        "CREATE TEMP TABLE _enrich (id varchar, scope text, stext text, spec jsonb) ON COMMIT DROP"
    )
    psycopg2.extras.execute_values(
        cur,
        "INSERT INTO _enrich VALUES %s",
        enrich,
        template="(%s, %s, %s, %s::jsonb)",
        page_size=2000,
    )
    cur.execute(
        """UPDATE oe_costs_item c SET metadata =
             jsonb_set(
               jsonb_set(c.metadata, '{scope}', to_jsonb(e.scope), true),
               '{search_text}', to_jsonb(e.stext), true)
           FROM _enrich e WHERE c.id = e.id"""
    )
    print(f"scope/search_text updated: {cur.rowcount}")
    cur.execute(
        """UPDATE oe_costs_item c SET metadata =
             jsonb_set(c.metadata, '{extra,spec}', e.spec, true)
           FROM _enrich e WHERE c.id = e.id AND e.spec IS NOT NULL"""
    )
    print(f"spec updated: {cur.rowcount} (extracted non-empty: {spec_filled})")

    cur.execute(
        """CREATE INDEX IF NOT EXISTS ix_costs_item_desc_unit_norm
           ON oe_costs_item (lower(btrim(description)), unit)
           WHERE is_active"""
    )
    print("index ok")
    conn.commit()
    cur.close()
    conn.close()

    # spot check
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()
    cur.execute(
        """SELECT scope_v, count(*) FROM (
             SELECT metadata->>'scope' AS scope_v FROM oe_costs_item
           ) t GROUP BY scope_v ORDER BY 2 DESC"""
    )
    for s, n in cur.fetchall():
        print(f"  scope={s}: {n}")
    cur.execute(
        """SELECT code, left(description,50), metadata->>'search_text' AS st,
                  metadata->>'scope' AS sc, metadata->'extra'->>'spec' AS sp
           FROM oe_costs_item WHERE metadata->'extra'->>'spec' IS NOT NULL
             AND is_active LIMIT 5"""
    )
    for r in cur.fetchall():
        print("  SPEC:", r)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
