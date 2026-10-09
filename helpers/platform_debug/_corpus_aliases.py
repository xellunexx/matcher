# Corpus translator: write each item's bill-language aliases into
# metadata.search_text so a KCC line's own words find the catalog row.
#
# The corpus speaks catalog-speak ("ПЕРДАШЕНА АРМИРАНА ЗАМАЗКА"), bills
# speak bill-speak ("шлайфана бетонова настилка"). Instead of translating
# every query at match time, translate every row once: a row's canonical
# concept tokens expand into every Bulgarian surface form the concept
# table knows, so "полагане на гранит", "монтаж на гранитни плочи" and
# "настилка от гранитогрес" all reach the same row.
#
# Aliases are DERIVED data: generated from the curated synonym table,
# marked via metadata.alias_kind="concept_surfaces". They only affect
# recall (which rows reach the pool); scoring still reads the real
# description.
import sys, re, json
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")

import psycopg2
from app.modules.cost_match.matcher import (
    _CONCEPT_SYNONYMS, _stem, canonical_tokens, normalize_text,
)

DSN = dict(host="127.0.0.1", port=49188, user="postgres",
           password="postgres", dbname="postgres")

# concept -> its Bulgarian surface forms (normalised). Latin-only forms
# are skipped - bills in this corpus are written in Cyrillic.
def _is_bg(form: str) -> bool:
    return any("\u0400" <= ch <= "\u04FF" for ch in form)

_CONCEPT_BG: dict[str, list[str]] = {}
for concept, forms in _CONCEPT_SYNONYMS.items():
    bg = [normalize_text(f) for f in forms if _is_bg(f)]
    bg = [f for f in bg if f]
    if bg:
        _CONCEPT_BG[concept] = bg

# Verb frames a bill uses to describe each price kind, so a work row is
# reachable by "полагане на ..." even when the catalog line is terse.
_SCOPE_FRAMES = {
    "material": "доставка",
    "work": "полагане монтаж направа изпълнение изграждане устройство",
    "composite": "полагане монтаж направа изпълнение изграждане устройство",
    "labor": "труд работа",
    "machine": "машина наем механизация",
}


def build_search_text(desc: str, scope: str) -> str:
    tokens = canonical_tokens(desc)
    alias: dict[str, None] = {}
    for tok in tokens:
        for form in _CONCEPT_BG.get(tok, ()):
            for word in form.split():
                alias.setdefault(word, None)
                stem = _stem(word)
                if len(stem) >= 4:
                    alias.setdefault(stem, None)
    for word in _SCOPE_FRAMES.get(scope, "").split():
        alias.setdefault(word, None)
    base = normalize_text(desc)
    extra = " ".join(sorted(alias))
    return f"{base} {extra}".strip()


conn = psycopg2.connect(**DSN)
cur = conn.cursor()
cur.execute("select id, description, coalesce(metadata->>'scope',''), unit from oe_costs_item")
rows = cur.fetchall()
print(f"rows: {len(rows)}")

cur.execute("create temp table _alias (id varchar primary key, st text)")
batch = []
for _id, desc, scope, unit in rows:
    st = build_search_text(desc or "", scope or "")
    batch.append((_id, st))
    if len(batch) >= 5000:
        cur.executemany("insert into _alias values (%s,%s)", batch)
        batch.clear()
if batch:
    cur.executemany("insert into _alias values (%s,%s)", batch)

cur.execute("""
    update oe_costs_item c
    set metadata = coalesce(c.metadata,'{}'::jsonb)
                   || jsonb_build_object('search_text', a.st,
                                         'alias_kind', 'concept_surfaces')
    from _alias a where c.id = a.id
""")
print("updated:", cur.rowcount)
conn.commit()

# sanity: the rows the failing lines need must now answer bill-speak
for probe in ["%шлайфан%", "%гранит%", "%пердаш%"]:
    cur.execute(
        "select count(*) from oe_costs_item where is_active and "
        "metadata->>'search_text' ilike %s", (probe,))
    print(probe, "->", cur.fetchone()[0], "active rows")
conn.close()
