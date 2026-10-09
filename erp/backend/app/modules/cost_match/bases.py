# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Named cost bases - the logical price databases a run can be pinned to.

``oe_costs_item.source`` is a provenance tag, not a clean database boundary:
Buildly rows carry ``reference_web`` because they were scraped from the web,
and the operator's own price list is spread across four tags. A *named base*
is the label a user actually thinks in - "match only against SEK", "only
against my operator prices" - resolved here to the physical predicate the
base scope applies, so every retrieval path (lexical, code lookup, vector
re-read) sees exactly the same universe.

A key that is not listed falls through to the raw ``source =`` equality the
base scope always had, so an existing pinned run (``cwicr``, ``manual``,
any concrete source tag) keeps meaning exactly what it meant.

Base map (2026-10-08 corpus census):

* ``sek``/``buildly`` - the СЕК unit-price reference as published through
  Buildly; the seed ids and the scraped codes share ``SEK-*``/``BUILDLY-*``
  prefixes, and the operator confirmed the two names denote one dataset.
* ``web`` - web-anchored prices that are NOT the SEK/Buildly dataset
  (supplier anchors and supplier lists).
* ``operator`` - the operator's own dictated price list and manual entries.
* ``opr`` - the operator price *files* (the
  ``costdb_seed_user_operator_prices*.json`` / ``costdb_seed_operator*.json``
  seeds): numbered ``OPR-``/``OPR2-``/``OPR3-`` rows and operator-sourced
  ``OPR-<uuid>`` rulings, which must remain reachable by the same base.
* ``cwicr`` - the main CWICR corpus.
* ``tenders`` - prices confirmed through won/filed tenders.
* ``learned`` - rows confirmed into the corpus by reviewer rulings.
* ``labor`` - the labour pricelist.
"""

from __future__ import annotations

from sqlalchemy import and_, or_

from app.modules.costs.models import CostItem

_OPERATOR_SOURCES = (
    "operator_pricelist",
    "manual_entry",
    "user_upload",
    "client_directive",
    "custom",
    "estimate_confirmed",
)

_WEB_SOURCES = (
    "reference_web",
    "supplier_web_anchor",
    "supplier_pricelist",
)


def _sek_base():
    # SEK and Buildly are one dataset through two doors: the 42-row
    # imported_workbook seed carries "SEK-*" ids, the scraped catalogue
    # carries "BUILDLY-*" codes.
    return or_(CostItem.code.like("SEK-%"), CostItem.code.like("BUILDLY-%"))


_BASES = {
    "sek": _sek_base,
    "buildly": _sek_base,
    "web": lambda: and_(
        CostItem.source.in_(_WEB_SOURCES),
        ~CostItem.code.like("BUILDLY-%"),
        ~CostItem.code.like("SEK-%"),
    ),
    "operator": lambda: CostItem.source.in_(_OPERATOR_SOURCES),
    "opr": lambda: and_(
        CostItem.source.in_(_OPERATOR_SOURCES),
        CostItem.code.op("~")(
            r"^OPR[0-9]*-([0-9]{6}|[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$"
        ),
    ),
    "cwicr": lambda: CostItem.source == "cwicr",
    "corpus": lambda: CostItem.source == "cwicr",
    "tenders": lambda: CostItem.source == "won_or_filed_tender",
    "learned": lambda: CostItem.source == "estimate_confirmed",
    "labor": lambda: CostItem.source == "labor_pricelist",
}

# The cascade UI offers these, in this order; anything else stays a raw
# source tag the way it always was.
BASE_CHOICES: tuple[str, ...] = (
    "sek",
    "opr",
    "operator",
    "web",
    "cwicr",
    "tenders",
    "learned",
    "labor",
)


def is_named_base(cost_source: str | None) -> bool:
    return bool(cost_source) and cost_source.strip().lower() in _BASES


def base_clause(cost_source: str):
    """The base predicate for a named base, or ``None`` for a raw source tag."""
    factory = _BASES.get((cost_source or "").strip().lower())
    return factory() if factory is not None else None
