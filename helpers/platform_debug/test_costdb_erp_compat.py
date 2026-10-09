"""Compatibility-vector tests: TenderOps cost_items -> ERP cost format.

Runs the real converter (scripts/migrate_costdb_to_erp.py) over the live
TenderOps SQLite corpus and asserts every converted row satisfies the ERP
ingest contract *before* anything is imported.

Run with the ERP backend venv so the real pydantic schemas validate:

    erp/backend/.venv/Scripts/python.exe test_costdb_erp_compat.py

Vectors covered:
  1.  Every row converts (count parity, no exceptions).
  2.  code <= 100 chars, (code, region) unique across the whole batch.
  3.  unit <= 20 chars, non-empty.
  4.  rate is a Decimal > 0 in the row's own currency (never fabricated).
  5.  currency is a non-empty ISO-style code from the observed set.
  6.  status mapping: active -> is_active=True; pending_review/retired -> False.
  7.  region mapping: '' -> None; 'BG' -> 'BG_SOFIA'; raw kept in metadata.
  8.  as_of: full ISO -> post.price_as_of date; partial -> None; raw preserved.
  9.  provenance round-trip: origin_kind/ref/note, validity, review_by,
      vat_included, derived_from_bgn, amount_eur, imported_at, extra_json all
      land in metadata verbatim.
  10. description carries the Bulgarian text; descriptions.bg mirrors it.
  11. TenderOps 'code' column (GOVSCR-*) -> metadata.tenderops_code + tags.
  12. source = origin_kind (<= 50 chars).
  13. Catalogs: one per non-empty source_key; name non-empty; currency is the
      dominant item currency (ISO-3).
  14. Pydantic CostItemCreate + CostCatalogCreate validate on EVERY payload.
  15. Determinism: two conversions produce identical output.
  16. NULL/optional fields: validity_to None, review_by '', derived_from_bgn
      None do not break conversion.
  17. BGN rows keep rate=original_amount + currency='BGN' (original_amount
      differs from amount_eur there - the EUR conversion is never substituted
      for the price the row actually carries).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent
# erp/backend first: ``app`` must resolve to the ERP backend package, not the
# repo's TenderOps ``app/``. The repo also has a ``scripts/`` dir that clashes
# with erp/backend/scripts, so the converter is loaded by file path instead.
sys.path.insert(0, str(REPO / "erp" / "backend"))

import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "migrate_costdb_to_erp", REPO / "scripts" / "migrate_costdb_to_erp.py"
)
_mig = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mig)  # type: ignore[union-attr]

REGION_MAP = _mig.REGION_MAP
TENDEROPS_DB = _mig.TENDEROPS_DB
convert = _mig.convert
convert_item = _mig.convert_item

from app.modules.costs.schemas import CostCatalogCreate, CostItemCreate  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILURES.append(msg)
        print(f"  FAIL {msg}")


def main() -> int:
    print(f"source db: {TENDEROPS_DB}")
    raw = convert()
    catalogs = raw["catalogs"]
    items = raw["items"]
    by_source = raw["by_source"]

    conn = sqlite3.connect(str(TENDEROPS_DB))
    conn.row_factory = sqlite3.Row
    src_rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM cost_items")}
    src_counts = dict(
        conn.execute("SELECT source_key, COUNT(*) FROM cost_items GROUP BY source_key").fetchall()
    )
    n_sources_with_items = sum(
        1 for (k,) in conn.execute("SELECT source_key FROM cost_sources")
        if src_counts.get(k, 0) > 0
    )
    conn.close()

    # 1. count parity
    check(len(items) == len(src_rows), f"item count {len(items)} != source {len(src_rows)}")
    check(len(catalogs) == n_sources_with_items,
          f"catalog count {len(catalogs)} != {n_sources_with_items} non-empty sources")

    # 2. code uniqueness per (code, region)
    pairs = Counter((i["create"]["code"], i["create"]["region"]) for i in items)
    dups = [p for p, n in pairs.items() if n > 1]
    check(not dups, f"{len(dups)} duplicate (code, region) pairs, e.g. {dups[:3]}")

    seen_ids = set()
    status_map = {"active": True, "pending_review": False, "retired": False}
    bgn_diff = 0
    pydantic_items = 0
    for pair in items:
        c = pair["create"]
        p = pair["post"]
        tid = c["metadata"]["tenderops_id"]
        seen_ids.add(tid)
        src = src_rows.get(tid)
        check(src is not None, f"item {tid} has no source row")
        if src is None:
            continue

        # 3. code
        check(c["code"] == tid, f"{tid}: code != tenderops id")
        check(1 <= len(c["code"]) <= 100, f"{tid}: code len {len(c['code'])}")

        # 4. unit
        check(c["unit"] == src["unit"], f"{tid}: unit mismatch")
        check(1 <= len(c["unit"]) <= 20, f"{tid}: unit len {len(c['unit'])}")

        # 5. rate = original_amount in own currency, > 0
        rate = Decimal(str(c["rate"]))
        check(rate > 0, f"{tid}: rate {rate} <= 0")
        if src["original_amount"] != src["amount_eur"]:
            bgn_diff += 1
            check(Decimal(str(c["rate"])) == Decimal(str(src["original_amount"])),
                  f"{tid}: rate {c['rate']} != original_amount {src['original_amount']}")
            check(c["currency"] == src["currency"], f"{tid}: currency changed on non-EUR row")
        check(c["currency"] == src["currency"], f"{tid}: currency {c['currency']} != {src['currency']}")
        check(0 < len(c["currency"]) <= 10, f"{tid}: bad currency")

        # 6. status mapping
        check(p["is_active"] is status_map[src["status"]],
              f"{tid}: status {src['status']} -> is_active {p['is_active']}")
        check(c["metadata"]["tenderops_status"] == src["status"], f"{tid}: status not preserved")

        # 7. region
        raw_region = (src["region"] or "").strip()
        check(c["region"] == REGION_MAP.get(raw_region),
              f"{tid}: region {raw_region!r} -> {c['region']!r}")
        check(c["metadata"]["tenderops_region"] == (raw_region or None),
              f"{tid}: raw region not preserved")

        # 8. as_of
        pao = p["price_as_of"]
        if pao is not None:
            check(isinstance(pao, date) and pao.isoformat() == src["as_of"],
                  f"{tid}: price_as_of {pao} != as_of {src['as_of']}")
        else:
            check(not (src["as_of"] or "").strip() or "-" not in src["as_of"] or len(src["as_of"]) != 10,
                  f"{tid}: full-date as_of {src['as_of']} dropped")
        check(c["metadata"]["as_of"] == src["as_of"], f"{tid}: raw as_of not preserved")

        # 9. provenance
        md = c["metadata"]
        check(md["origin_kind"] == src["origin_kind"], f"{tid}: origin_kind")
        check(md["origin_ref"] == (src["origin_ref"] or None), f"{tid}: origin_ref")
        check(md["origin_note"] == (src["origin_note"] or None), f"{tid}: origin_note")
        check(md["vat_included"] == bool(src["vat_included"]), f"{tid}: vat_included")
        check(md["amount_eur"] == src["amount_eur"], f"{tid}: amount_eur")
        check(md["derived_from_bgn"] == src["derived_from_bgn"], f"{tid}: derived_from_bgn")
        check(md["validity_from"] == (src["validity_from"] or None), f"{tid}: validity_from")
        check(md["validity_to"] == (src["validity_to"] or None), f"{tid}: validity_to")
        check(md["review_by"] == (src["review_by"] or None), f"{tid}: review_by")
        check(md["tenderops_source_key"] == src["source_key"], f"{tid}: source_key")
        check(md["tenderops_imported_at"] == src["imported_at"], f"{tid}: imported_at")
        extra_src = json.loads(src["extra_json"]) if src["extra_json"] else {}
        check(md["extra"] == extra_src, f"{tid}: extra_json not verbatim")

        # 10. Bulgarian text
        check(c["description"] == (src["name"] or src["desc"]), f"{tid}: description")
        check(c["descriptions"].get("bg") == c["description"], f"{tid}: descriptions.bg")

        # 11. tenderops code column
        tcode = (src["code"] or "").strip()
        if tcode:
            check(md["tenderops_code"] == tcode, f"{tid}: tenderops_code")
            check(tcode in c["tags"], f"{tid}: code not in tags")

        # 12. source
        check(c["source"] == src["origin_kind"], f"{tid}: source")
        check(0 < len(c["source"]) <= 50, f"{tid}: source len")

        # 13. classification
        cat = (src["category"] or "").strip()
        sec = (src["section"] or "").strip()
        if cat:
            check(c["classification"].get("collection") == cat, f"{tid}: classification.collection")
        if sec:
            check(c["classification"].get("section") == sec, f"{tid}: classification.section")

        # 14. pydantic contract
        try:
            CostItemCreate.model_validate(c)
            pydantic_items += 1
        except Exception as e:  # noqa: BLE001
            FAILURES.append(f"{tid}: CostItemCreate rejected: {e}")

    check(len(seen_ids) == len(src_rows), "not all source rows converted")

    # 13b. catalogs
    for cat in catalogs:
        check(bool(cat["name"].strip()), f"catalog {cat['source_key']}: empty name")
        check(len(cat["currency"]) == 3 and cat["currency"].isalpha(),
              f"catalog {cat['source_key']}: bad currency {cat['currency']!r}")
        items_here = by_source[cat["source_key"]]
        dom = Counter(i["create"]["currency"] for i in items_here).most_common(1)[0][0]
        check(cat["currency"] == dom, f"catalog {cat['source_key']}: currency {cat['currency']} != dominant {dom}")
        try:
            CostCatalogCreate.model_validate(
                {"name": cat["name"], "description": cat["description"], "currency": cat["currency"]}
            )
        except Exception as e:  # noqa: BLE001
            FAILURES.append(f"catalog {cat['source_key']}: CostCatalogCreate rejected: {e}")

    # 15. determinism
    raw2 = convert()
    check(
        json.dumps(raw["items"], sort_keys=True, default=str)
        == json.dumps(raw2["items"], sort_keys=True, default=str),
        "conversion is not deterministic",
    )

    # 17. BGN parity
    n_bgn = sum(1 for r in src_rows.values() if r["currency"] != "EUR")
    check(bgn_diff == n_bgn, f"non-EUR rows {bgn_diff} != {n_bgn}")

    print()
    print(f"items converted+validated: {len(items)} (pydantic ok: {pydantic_items})")
    print(f"catalogs: {len(catalogs)}   non-EUR rows: {bgn_diff}")
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURES:")
        for f in FAILURES[:40]:
            print("  -", f)
        return 1
    print("\nALL COMPATIBILITY VECTORS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
