# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""KCC golden harness: price a ``-test`` workbook against an imported Bulgarian
cost corpus and compare against its priced gold workbook.

What this proves
----------------
The whole pricing path end to end, on real artifacts, against the real service:

1.  a ``costdb_seed_*.json`` Bulgarian price db is ingested into
    ``oe_costs_item`` (field mapping mirrors the legacy loader
    ``tenderops/platform/app/costdb.py::_item_from_json``);
2.  the test КСС is parsed by the production ``ExcelImporter``;
3.  every leaf line is scored by ``CostMatchService._score_line`` - the same
    code ``run_boq_match`` runs;
4.  the produced suggestion per line is compared against the human-priced
    gold workbook row-aligned on the bill's own ordinal.

Output: a priced workbook copy and a CSV/JSON report beside the gold file
(``<name>-priced-<tag>.xlsx`` / ``<name>-report-<tag>.csv|.json``), plus a
stdout summary whose last line is the machine verdict::

    VERDICT ok=0.87 tol=0.10 rows=409 priced=402 total_dev=+3.2%

The seeds carry ``status: pending_review``, so by the platform contract
nothing auto-applies; the harness therefore measures the *suggestion* a
reviewer would confirm - exactly the price quality in question.

Usage (from erp/backend, with the project venv)::

    python scripts/kcc_golden.py
    python scripts/kcc_golden.py --seeds path/to/costdb_seed_a.json path/to/b.json

Environment: ``OE_KCC_SEMANTIC=1`` enables the semantic-assist veto if a
vector backend is reachable; otherwise the deterministic path runs untouched.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "kcc"
EURBGN = Decimal("1.95583")

# Seeds whose codes are the operator price books (OPR-/OPR2-/OPR3-); anything
# from the user uploads is company-actual evidence. SEK is a reference book.
_SOURCE_TAG = {
    "costdb_seed_operator.json": "operator_pricelist",
    "costdb_seed_operator2.json": "operator_pricelist",
    "costdb_seed_user_operator_prices.json": "operator_pricelist",
    "costdb_seed_user_operator_prices2.json": "operator_pricelist",
    "costdb_seed_user_operator_prices3.json": "operator_pricelist",
    "costdb_seed_sek_2026.json": "reference_web",
}


def _fail(msg: str) -> None:
    print(f"FATAL: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _boot_pg() -> Path:
    """Throwaway embedded PG cluster; must run before any app import."""
    data_dir = Path(tempfile.mkdtemp(prefix="oe_kcc_golden_pg_"))
    from app.core import embedded_pg

    if not embedded_pg.boot(data_dir):
        _fail(f"embedded PostgreSQL refused to boot: {embedded_pg.last_fatal_detail()}")

    # Harness hygiene: the cluster and its data dir leave with this process.
    def _cleanup() -> None:
        import shutil

        try:
            embedded_pg.shutdown()
        except Exception:
            pass
        shutil.rmtree(data_dir, ignore_errors=True)

    import atexit

    atexit.register(_cleanup)
    return data_dir


async def _seed_corpus(session, seed_paths: list[Path]) -> int:
    """Map costdb_seed JSON rows onto CostItem, money untouched in currency.

    Mirrors ``costdb._item_from_json``: BGN rows are stored as quoted (the
    service converts at the EUR peg on the way out, exactly like production),
    ``status`` maps onto ``metadata.tenderops_status`` and ``origin.kind``
    onto ``metadata.origin_kind`` so reference rows stay flagged REF.

    Seed files share codes - the ``user_operator_prices*`` files are re-uploads
    of the same operator books (same ``OPR-`` ids, newer editorial state).
    ``oe_costs_item`` holds ``uq_costs_code_region (code, region)``, so rows
    are keyed on (code, region) and a row from a LATER ``--seeds`` file
    replaces an earlier one, mirroring "re-import replaces" semantics.
    """
    from app.modules.costs.models import CostItem

    staged: dict[tuple[str, str], dict] = {}
    anon = 0
    for path in seed_paths:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        rows = data if isinstance(data, list) else data.get("rows") or data.get("prices") or []
        source_tag = _SOURCE_TAG.get(path.name, "operator_pricelist")
        for item in rows:
            if not isinstance(item, dict):
                continue
            money = item.get("money") or {}
            amount = money.get("amount")
            if not isinstance(amount, (int, float)) or float(amount) <= 0:
                continue
            desc = str(item.get("desc") or item.get("name") or "").strip()
            if len(desc) < 3:
                continue
            unit = str(item.get("unit") or "").strip()
            code = str(item.get("code") or item.get("id") or "").strip()
            region = str(item.get("region") or "BG")
            if not code:
                code = f"SEED-{path.stem}-{anon}"
                anon += 1
            origin = item.get("origin") or {}
            currency = str(money.get("currency") or "EUR").strip().upper()
            staged[(code, region)] = {
                "code": code,
                "description": desc,
                "descriptions": {},
                "unit": unit,
                "rate": repr(float(amount)),
                "currency": currency,
                "source": source_tag,
                "classification": {},
                "components": [],
                "tags": [],
                "region": region,
                "catalog_id": None,
                "is_active": True,
                "metadata_": {
                    "tenderops_status": str(item.get("status") or ""),
                    "origin_kind": str(origin.get("kind") or ""),
                    "seed_file": path.name,
                },
            }
    # Later file wins on a shared (code, region) - the staging dict already
    # holds the last write.
    for rec in staged.values():
        session.add(CostItem(**rec))
    await session.flush()
    return len(staged)


def _parse_gold(gold_path: Path) -> dict[str, dict]:
    """(normalized name, unit) -> gold price row from the priced gold КСС.

    The bill's own ordinal column ("№ подч.") is not recognised by the
    generic header aliases, so alignment keys on the CONTENT instead: the
    description + unit pair is what a reviewer would join on. Duplicate
    (name, unit) pairs are recorded in ``_collisions`` - the harness reports
    them rather than silently measuring the last one.
    """
    import openpyxl

    from app.modules.cost_match.matcher import normalize_text

    ws = openpyxl.load_workbook(gold_path, read_only=True, data_only=True).worksheets[0]
    out: dict[tuple[str, str], dict] = {}
    collisions: list[str] = []
    for row in ws.iter_rows(values_only=True):
        name = row[2] if len(row) > 2 else None
        unit = row[4] if len(row) > 4 else None
        qty = row[5] if len(row) > 5 else None
        rate = row[6] if len(row) > 6 else None
        ordinal = row[1] if len(row) > 1 else None
        if not name or not str(name).strip():
            continue
        key = (normalize_text(str(name)), str(unit or "").strip().lower())
        entry = {
            "ordinal": str(ordinal or "").strip(),
            "name": str(name).strip(),
            "unit": str(unit or "").strip(),
            "qty": float(qty) if isinstance(qty, (int, float)) else None,
            "rate": float(rate) if isinstance(rate, (int, float)) else None,
        }
        if key in out and out[key]["rate"] != entry["rate"]:
            collisions.append(f"{key}: {out[key]['rate']} vs {entry['rate']} ({entry['ordinal']})")
        out[key] = entry
    out["_collisions"] = collisions
    return out


async def _score_world(args, test_path: Path, gold_path: Path, seed_paths: list[Path]):
    """Boot embedded PG, seed the corpus, parse the bill, score every leaf.

        Returns ``(gold, collisions, produced, prod_collisions)`` ready for the
        comparison stage; nothing is written to disk here.
    """
    _boot_pg()

    from app.database import Base, async_session_factory, engine
    from app.modules.boq.importers.excel import ExcelImporter
    from app.modules.cost_match.service import CostMatchService

    # Register every module's models on Base.metadata before create_all,
    # mirroring alembic/env.py — cross-module FK targets only exist once the
    # owning module is imported.
    import importlib
    import pkgutil

    from app import modules as _modules_pkg

    for _entry in pkgutil.iter_modules([os.path.dirname(_modules_pkg.__file__)]):
        if _entry.ispkg:
            try:
                importlib.import_module(f"app.modules.{_entry.name}.models")
            except Exception:
                pass
    try:
        from app.core import audit_log as _audit_log_core  # noqa: F401
    except Exception:
        pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        n_items = await _seed_corpus(session, seed_paths)
        await session.commit()
        print(f"corpus seeded: {n_items} items from {len(seed_paths)} file(s)")

        boq = await ExcelImporter.parse(test_path.read_bytes(), locale="bg")
        leaves = [
            p
            for p in boq.positions
            if not (getattr(p, "metadata", None) or {}).get("section_header")
            and (p.unit or "").strip().lower() not in ("", "section")
            and (p.description or "").strip()
        ]
        print(f"parsed positions: {len(boq.positions)}, leaf rows: {len(leaves)}")
        if args.only:
            needles = [n.strip().lower() for n in args.only.split(",") if n.strip()]
            leaves = [p for p in leaves if any(n in (p.description or "").lower() for n in needles)]
            print(f"probe mode: {len(leaves)} row(s) containing any of {needles}")

        svc = CostMatchService(session)
        run = SimpleNamespace(
            id=None,
            cost_source="all",
            region="BG",
            catalog_id=None,
            candidate_limit=args.candidate_limit,
            source_locale="bg",
        )

        gold = _parse_gold(gold_path)
        collisions = gold.pop("_collisions")
        produced: dict[tuple[str, str], dict] = {}
        prod_collisions: list[str] = []
        from app.modules.cost_match.matcher import normalize_text

        for p in leaves:
            r = await svc._score_line(
                description=p.description or "",
                unit=p.unit or "",
                source_ref="",
                run=run,
            )
            if args.only:
                print(f"LINE: {(p.description or '')[:70]!r} unit={p.unit!r} -> {r['suggested_code']} {r['suggested_unit']} {r['suggested_rate']} tier={r['tier']} reasons={r['reason_codes'][-4:]}")
                for alt in r["alternatives"]:
                    print(f"      alt {alt['code']:<26} unit={alt['unit']!r:>8} rate={alt['rate']:<10} conf={alt['confidence']} band={alt['band']}")
            key = (normalize_text(p.description or ""), (p.unit or "").strip().lower())
            if key in produced:
                prod_collisions.append(key[0][:60])
            # The service reports the corpus rate in the *candidate's* unit;
            # the write-back path converts through ``unit_rate_factor`` when
            # it prices the position (км rate -> м line divides by 1000, the
            # tonne rate onto a per-kg line divides by 1000, ...). The harness
            # must judge the number that would actually land on the bill.
            rate_line: float | None = None
            unit_blocked = False
            if r["suggested_rate"] is not None:
                from app.modules.cost_match.matcher import normalize_unit, unit_rate_factor

                factor = unit_rate_factor(r["suggested_unit"], p.unit)
                if factor is not None:
                    rate_line = float(Decimal(r["suggested_rate"]) * factor)
                elif normalize_unit(p.unit) is not None or normalize_unit(r["suggested_unit"]) is not None:
                    # No honest conversion between two recognisable units -
                    # production withholds this price (``corpus_unit_mismatch``).
                    unit_blocked = True
                else:
                    rate_line = float(r["suggested_rate"])
            produced[key] = {
                "desc": (p.description or "").strip(),
                "unit": (p.unit or "").strip(),
                "tier": ("withheld:" + r["tier"]) if unit_blocked else r["tier"],
                "confidence": float(r["confidence"]),
                "rate": rate_line,
                "currency": r["suggested_currency"],
                "reasons": list(r["reason_codes"]),
                "suggested_desc": r["suggested_description"],
                "suggested_code": r["suggested_code"],
                "suggested_unit": r["suggested_unit"],
            }
        return gold, collisions, produced, prod_collisions


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--test", default=str(FIXTURES / "kcc9-test.xlsx"))
    ap.add_argument("--gold", default=str(FIXTURES / "kcc9.xlsx"))
    ap.add_argument(
        "--seeds",
        nargs="+",
        default=[str(FIXTURES / "costdb_seed_operator.json"), str(FIXTURES / "costdb_seed_user_operator_prices3.json")],
    )
    ap.add_argument("--candidate-limit", type=int, default=40)
    ap.add_argument("--tolerance", type=float, default=0.10)
    ap.add_argument("--only", default="", help="score only leaf rows whose description contains this substring (probe mode)")
    ap.add_argument("--from-checkpoint", default="", help="skip scoring entirely; rebuild report/artifacts from a checkpoint json (fast artifact iteration)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    test_path, gold_path = Path(args.test), Path(args.gold)
    seed_paths = [Path(s) for s in args.seeds]
    for p in (test_path, gold_path, *seed_paths):
        if not p.exists():
            _fail(f"missing input: {p}")
    tag = args.tag or "-".join(p.stem.replace("costdb_seed_", "") for p in seed_paths)[:60]

    if args.from_checkpoint:
        # Artifact regeneration without rescoring: everything compare/report
        # needs is already inside the checkpoint.
        ck = json.loads(Path(args.from_checkpoint).read_text(encoding="utf-8"))
        gold = {tuple(k.rsplit("|", 1)): v for k, v in ck["gold"].items()}
        collisions = ck.get("collisions", [])
        prod_collisions = ck.get("prod_collisions", [])
        produced = {tuple(k.rsplit("|", 1)): v for k, v in ck["produced"].items()}
        print(f"loaded checkpoint: {len(produced)} produced rows, {len(gold)} gold keys")
    else:
        gold, collisions, produced, prod_collisions = await _score_world(args, test_path, gold_path, seed_paths)

    # ── Compare, aligned on (normalized description, unit) ───────────────
    rows_report, within, off, unpriced_gold = [], 0, 0, 0
    gold_priced = 0
    gold_sum = Decimal(0)
    prod_sum = Decimal(0)
    for key, g in gold.items():
        if g["rate"] is None:
            continue
        gold_priced += 1
        gold_sum += Decimal(str(g["rate"])) * Decimal(str(g["qty"] or 1))
        pr = produced.get(key)
        if pr is None or pr["rate"] is None:
            unpriced_gold += 1
            rows_report.append({"ordinal": g["ordinal"], **g, "produced_rate": None, "dev": None,
                                "tier": (pr or {}).get("tier", "not-parsed"), "reasons": (pr or {}).get("reasons", [])})
            continue
        prod_sum += Decimal(str(pr["rate"])) * Decimal(str(g["qty"] or 1))
        dev = (Decimal(str(pr["rate"])) - Decimal(str(g["rate"]))) / Decimal(str(g["rate"]))
        ok = abs(dev) <= Decimal(str(args.tolerance))
        within += 1 if ok else 0
        off += 0 if ok else 1
        rows_report.append({"ordinal": g["ordinal"], **g, "produced_rate": pr["rate"], "dev": float(dev),
                            "tier": pr["tier"], "confidence": pr["confidence"], "reasons": pr["reasons"],
                            "suggested_code": pr["suggested_code"], "suggested_desc": pr["suggested_desc"][:120]})

    out_dir = gold_path.parent
    checkpoint = out_dir / f"{gold_path.stem}-checkpoint-{tag}.json"
    checkpoint.write_text(
        json.dumps(
            {
                "gold": {"|".join(k): v for k, v in gold.items()},
                "collisions": collisions,
                "prod_collisions": prod_collisions,
                "produced": {"|".join(k): v for k, v in produced.items()},
                "seed_files": [p.name for p in seed_paths],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"checkpoint: {checkpoint}")

    csv_path = out_dir / f"{gold_path.stem}-report-{tag}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["ordinal", "name", "unit", "qty", "gold_rate", "produced_rate", "dev_%",
                    "tier", "confidence", "reasons", "suggested_code", "suggested_desc"])
        def _key(r):
            parts = []
            for x in r["ordinal"].replace(".", " ").split():
                parts.append((0, int(x), "") if x.isdigit() else (1, 0, x))
            return parts
        for r in sorted(rows_report, key=_key):
            w.writerow([r["ordinal"], r["name"][:80], r["unit"], r["qty"], r["rate"],
                        r.get("produced_rate"), None if r.get("dev") is None else round(r["dev"] * 100, 1),
                        r["tier"], r.get("confidence", ""), ";".join(r["reasons"]), r.get("suggested_code", ""), r.get("suggested_desc", "")])

    # The priced workbook the user asked for: the test bill with the matched
    # unit price and line total filled into the same two columns the gold
    # carries ("Ед. цена (евро)", "Обща ст-ст (евро)"), laid out content-
    # aligned like the comparison. Rows with no suggestion stay empty -
    # unresolved, never guessed.
    import openpyxl

    from app.modules.cost_match.matcher import normalize_text

    wb = openpyxl.load_workbook(test_path)
    ws = wb.worksheets[0]
    priced_rows = 0
    for row in ws.iter_rows():
        name = row[2].value if len(row) > 2 else None
        unit = row[4].value if len(row) > 4 else None
        qty = row[5].value if len(row) > 5 else None
        if not name or not str(name).strip():
            continue
        key = (normalize_text(str(name)), str(unit or "").strip().lower())
        pr = produced.get(key)
        if pr is None or pr["rate"] is None:
            continue
        price_cell = row[6] if len(row) > 6 else None
        total_cell = row[7] if len(row) > 7 else None
        if price_cell is not None:
            price_cell.value = round(pr["rate"], 4)
            priced_rows += 1
        if total_cell is not None and isinstance(qty, (int, float)):
            total_cell.value = round(pr["rate"] * float(qty), 2)
    priced_path = out_dir / f"{gold_path.stem.replace('kcc9', 'kcc9-priced')}-{tag}.xlsx"
    wb.save(priced_path)
    print(f"priced workbook: {priced_path} ({priced_rows} rows priced)")

    compared = within + off
    ok_share = (within / compared) if compared else 0.0
    total_dev = float((prod_sum - gold_sum) / gold_sum) if gold_sum else 0.0
    print(f"gold priced rows: {gold_priced} · compared: {compared} · no-suggestion: {unpriced_gold}")
    print(f"within ±{args.tolerance:.0%}: {within}/{compared} = {ok_share:.1%}")
    print(f"total: gold {gold_sum:.2f} EUR · produced {prod_sum:.2f} EUR · dev {total_dev:+.1%}")
    print(f"report: {csv_path}")
    verdict_ok = ok_share >= 0.85 and abs(total_dev) <= args.tolerance
    print(f"VERDICT ok={ok_share:.2f} tol={args.tolerance:.2f} rows={gold_priced} "
          f"priced={sum(1 for v in produced.values() if v['rate'] is not None)} "
          f"total_dev={total_dev:+.1%} {'PASS' if verdict_ok else 'FAIL'}")
    return 0 if verdict_ok else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
