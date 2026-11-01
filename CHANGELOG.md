# Changelog

## 2026-11-02 — DB source processing runbook + consensus-pool crawler

- Added `DB_SOURCES.md` — the operating runbook for turning raw DB sources
  (e.g. the 12 GB tsc heap) into priced corpus rows: 6 stages with gates
  (sort+inventory → extract → normalize → register+load → verify+vocab-training
  → promote+log), source-conditional file rules (PDF/.doc/.docx/unpriced-КСС
  rules apply to EOP tender dumps only), the SEK norms pool + hooked-consensus
  mechanism (every unique KCC line hooked to a norm is a candidate; pool median
  IS the norm price; per-norm median computed on 2026-dated lines only), the
  SEK vs KSO norm-scheme distinction (never pooled together), and the live-lane
  split (ERP Postgres `oe_costs_item`/`oe_cost_match` is the production corpus;
  pricedb→build_db→sqlite is the legacy offline/eval lane).
- Added `tools/build_consensus_pools.py` — deterministic crawler that extracts
  every sheet of analysis workbooks (анализни цени), hooks each KCC line to its
  `Шифър` norm code, classifies codes by scheme (sek/kso/other), and emits one
  consensus pool per unique norm with `median_eur` + `median_eur_2026`,
  dated/undated line counts, unhooked analyses (no `Шифър`), and a drop log.
  Verified on 30111.xlsx + 40111.xlsx: 69 sek pools / 176 hooked lines /
  4,020 unhooked analyses / 0 silently skipped sheets.
