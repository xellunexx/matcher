# DB source processing — operating runbook (DRAFT — nothing activated)

How a raw source (a folder, dump, heap — e.g. the TSC 12 GB heap) becomes pricedb rows
the matcher can honestly use. Follow the stages in order. Every stage has a gate —
don't move on until the gate is green.

---

## The map (memorize this one picture)

```
RAW HEAP (read-only)
   │  Stage 1  sort + inventory → zips per tender ID+date; file rules per source
   │                             type (see Stage 1); manifest.md
   │  Stage 2  extract          → work/<src>/rows.jsonl  (+ drop log)
   │  Stage 3  normalize        → PG corpus row shape (oe_costs_item)
   │  Stage 4  register+load    → ERP PG lane (oe_costs / oe_cost_match)
   │  Stage 5  verify+train     → FULL vocab-training on the loaded rows; zero
   │                             no-recall lines; work/<src>_ingest_report.md
   │  Stage 6  promote+log      → status, CHANGELOG.md, one commit
   ▼
MATCHER (retrieval → scoring → commit gate → price, or unpriced)
```

**Two lanes — don't mix them.** The **live lane is the ERP Postgres corpus**
(`oe_costs_item` + the `oe_cost_match` module — `oe_cost_match_run/result/decision`).
New DB work lands there. The `pricedb/*.json` → `tools/build_db.py` →
`work/tenderops.sqlite3` path (Stages 3–4's seed-JSON machinery below) is the
**legacy offline lane** — still the home of the evals (`eval_median.py`,
`eval_embed.py`) and local vocab-training, but it is NOT the production corpus.
`work/` is scratch, gitignored.

---

## Ground rules (never violated)

1. **Originals are read-only.** Heap files are only moved/deleted per Stage 1 — never edited. Produced data lives in `work/` or `pricedb/`.
2. **Every row carries provenance** — where it came from, file + position. A row without provenance is garbage; drop it and log the drop.
3. **Nothing is fabricated.** No invented prices, units, dates, aliases. A row without an honest price does not get one.
4. **Unverified → `pending_review`.** Promotion to `active` requires a measured check (Stage 5), never vibes.
5. **Fix mechanisms, not lines.** No one-off hardcodes (ADHOC ban).
6. **Every processed source gets a CHANGELOG.md entry.**

---

## Stage 1 — Sort + inventory the heap (do NOT extract yet)

The heap is a pile of tender archives. First make it navigable:

- **Arrange zips per tender ID + date** — one folder per tender: `<tender_id>_<date>/`. Everything belonging to one tender goes under its ID.
- **File rules are source-conditional — the ones below apply to EOP tender-archive dumps (the tsc heap class), not every source:**
  - **PDFs: never extract.** Move them into the pdf folder — in a tender dump they are documents (drawings, specs, invoices), not price rows.
  - **Word files (.doc/.docx): delete** in a tender dump — no price rows for this pipeline.
  - **Unpriced КСС/КС files: delete** in a tender dump — an unpriced bill is input, not evidence. (Priced ones are kept — they're the best evidence there is.)
  - **For any other source type** (a supplier's PDF pricelist, a Word price book, a scanned catalog) these files may BE the evidence — do not park/delete by default; flag the source for the operator and record the verdict in the manifest.
- Then inventory: formats, counts, sizes; sample ~20 workbooks by eye.
- Write `work/<src>/manifest.md`: file → type → verdict (price-bearing / pdf / deleted), est. rows.

**Gate:** heap arranged (tender folders, pdfs parked, junk deleted); manifest exists; every file is classified or listed as undecided — never silently skipped.

## Stage 2 — Extract (workbooks → rows)

- Extraction is a **script** in `tools/` (e.g. `extract_<src>.py`) — rerunnable, no manual edits.
- **Extract EVERY sheet of every .xlsx/.xls** — prices hide in later sheets.
- **A sheet that returns empty is a lie — doubt it and check it.** Sheets in these workbooks are never truly empty: re-read with another engine/offset, check hidden rows/columns, check merged cells, check if it's a scanned image inside the sheet. Only after proving it's empty may it be logged as empty.
- Stream, never load the whole heap. Write `work/<src>/rows.jsonl` — one JSON row per line.
- Every row keeps its pointer: which file, which sheet, which row.
- Sample first: extract 100–500 rows, eyeball, then the full pass.
- Log every dropped row + reason into `work/<src>/drops.log`. The drop log IS the audit trail.
- Dedupe on (normalized text, unit, price, source pointer).

**Gate:** rows.jsonl exists, every sheet accounted for (rows or proven-empty), drops logged, sample eyeballed.

## Stage 3 — Normalize

**Live lane (PG):** normalize rows toward the ERP Postgres corpus —
`oe_costs_item` under the `oe_costs`/`oe_cost_match` modules. Work out the exact
column mapping against `erp/backend/app/modules/cost_match/` at run time; the
schema below is the *legacy* lane's contract and the conceptual shape carries
over (provenance, unit alias, positive EUR money, pending status).

**Legacy lane (costdb sqlite — evals only):** the seed-JSON contract:

Real seed item shape (checked against `pricedb/costdb_seed_*.json`):

```json
{
  "id": "OPR-000123",
  "code": "OPR-000123",
  "name": "short label",
  "desc": "full original wording — never rewritten",
  "category": "operator/2-2024-09-11-...",
  "unit": "м³",
  "money": {"amount": 160.53, "currency": "EUR", "vatIncluded": false, "asOf": "2024-09-11"},
  "origin": {"kind": "operator_pricelist", "ref": "2-2024-09-11-...xls", "note": ""},
  "validity": {"from": "2024-09-11", "to": null},
  "status": "pending_review",
  "region": "BG",
  "type": "priced_offer",
  "approvals": []
}
```

Fields the importer actually reads (`_item_from_json`):
- `id`, `code` — the row's own identity (there is NO `row_Id` field; see below).
- `name`/`desc` — original wording preserved (desc ≥3 chars or the row is dropped).
- `unit` — through `UNIT_ALIASES_DEFAULT` (бр→PIECE, м→M, м²→M2, м³→M3, кг/т, ч→HOUR, ден→DAY, паушал→LSUM). Unmapped → drop+log.
- `money.amount` — positive only (≤0 dropped by importer). `currency` EUR or BGN (BGN→EUR at import, ÷1.95583; original kept). `vatIncluded`, `asOf`.
- `origin{kind, ref, note}` — provenance of the row: what it is (`operator_pricelist`, `imported_workbook`, `supplier_web_anchor`, `won_tender`, ...), which file (`ref`), and a free `note`.
- `validity{from,to}`, `status`, `region`, `category`, `section`, `approvals[]`.

About `row_Id`: **the seed schema has none** — `id`/`code` IS the row identity. The importer
does support an optional `source_record` passthrough (lands in `extra_json` verbatim) —
that's the right place for a source-row pointer (file/sheet/row no.) if we want one; no
current seed uses it yet. The corpus CSV tracks the same idea as `learned_from_position`.

**Gate:** rows validate against the lane's schema and the loader accepts every
row (spot-check a build).

## Stage 4 — Register and load

**Live lane (PG):** load into the ERP Postgres corpus (`oe_costs_item`) via the
`oe_costs`/`oe_cost_match` ingest path — rows must carry source provenance so
`oe_costs_item.source` audits back to the file. Identify the module's ingest
entrypoint at run time; do not hand-write rows.

**Legacy lane (costdb sqlite):**
- Save as `pricedb/costdb_seed_<name>.json` (list or `{"rows":[...]}`).
- Add a `SOURCE_META` entry in `app/costdb.py`: `source_key`, `source_type`, `display_name`, `as_of`.
  (Unregistered `costdb_seed_*.json` still imports as `imported_<key>`; `costdb_seed_user_*` → `company_actual`. Register anyway — display names and as_of matter in audits.)
- `python tools/build_db.py` → check printed per-source row counts and `costdb.stats`.
- Rebuild is idempotent (sha-keyed), `force=True` rebuilds — safe to rerun.

**Gate:** rows land under the right `source_key`/provenance tag in the target
store; retrieval counts match loaded counts.

## Stage 5 — Verify + full vocab-training (MANDATORY for every new seed)

A source in the DB that retrieval never surfaces is dead weight. **Every new seed —
no exceptions — requires a full `vocab-training` run against it** (the skill encodes
the convention). Test → verify → confirm. The acceptance bar: **zero "no-recall"
lines** — a line where matcher retrieval returns no candidates at all
(`no_candidates` / empty `search_candidates` result) is a no-recall failure that must
be trained away, not logged and left.

The loop, per KCC line:
1. **Matcher pass** — run the line through the matcher, log the full result.
2. **DB pass** — search the same line against the DB as plain text; log every reply.
3. **Adapt until found** — if the DB search misses the right candidate, adapt the
   search vectors until the best possible candidate is found. "No candidates" is
   never accepted.
4. **Compare + explain** — was the DB's best candidate considered by the matcher?
   Vetoed → investigate WHY. Not sure → not-sure list. Never considered → miss.
5. **Teach vocab** — fix the vocabulary so the matcher sees that candidate next time
   (gate-level fixes, no line hardcodes).
6. **Log per line** into the output file — the operator checks it.

Then re-run the affected lines through the matcher: each previously-missed line must
now retrieve its candidate. **Confirm: no `no-recall` line remains.** If one remains
after the vocab fixes — the job is not done; keep going.

`pending_review` rows ARE retrievable (`include_pending=True`) and can commit only as
**advisory reference prices** (`accept_pending`) — never as the spent price. An
unverified source informs but cannot fabricate a price.

**Gate:** full vocab-training output file exists (per-line proof); every no-recall
line trained until it recalls; `work/<src>_ingest_report.md` written: heap rows →
extracted → dropped(why) → in DB → recalled in vocab-training. Real numbers only;
"not measured" where not run.

## Stage 6 — Promote, document, commit

- `status: "active"` only for what Stage 5 proved.
- `CHANGELOG.md`: source name, counts, decisions, unresolved items.
- One commit/PR: seed JSON + SOURCE_META + extractor + changelog.

---

## SEK norms pool and the hooked consensus

Analysis workbooks (анализни цени — one sheet per KCC position, e.g. 30111.xlsx with
1,740 sheets) serve **two purposes**:

### 1. The SEK norms pool — and the norm-scheme distinction
The files carry **two different norm schemes — never pool them together:**

- **SEK scheme** — the `Шифър (код)` norm codes: digit runs `3323xxxxxx`,
  `80xx/81xx xxxxx`, `СЕК`-prefixed (`СЕК26.219`, `СЕК03.009`), `*`-starred
  (`*332303293` = norm applied outside its rated range).
- **KSO scheme** — dotted object codes like `07.910` (the КСО classifier). A KSO
  code is a *classification*, not a SEK norm: a `KSO 07.910` line must never join
  a `SEK 33xxxx` pool.

The crawler classifies every code by scheme (`sek` / `kso` / `other`) and pools
per **(scheme, code)** pair — each unique SEK norm gets its own pool, KSO codes
their own, unrecognized schemes their own counted bucket.

Each analysis sheet names the norm the position was priced against — the
`Шифър (код)` column. Together the analyses build the SEK norms pool: every
unique norm code, its wording, unit and norm price (plus its
труд/материали/механизация breakdown and доп.разходи where present).

**Before pooling — each SEK norm gets its OWN price median, 2026 only.** Across all
xlsx/xls files, collect every line hooked to a unique norm and form the norm's own
median unit price from **2026-dated lines only** — that median is the norm's
authoritative price baseline for the consensus. Lines whose source has no usable
date (no tender ID+date from the Stage-1 zip naming, no `--file-dates` entry) are
still extracted and counted as `undated`, but excluded from the 2026 median —
never silently dropped. `tools/build_consensus_pools.py` writes
`median_eur_2026`, `lines_dated`, `lines_undated` per norm.

### 2. Corpus candidates consensus per unique SEK norm
For every KCC line across **all EOP-pulled tenders**, whenever the line has an
analysis-file counterpart that includes a SEK norm for that line:

- **Every unique KCC line hooked to a norm becomes a candidate** in that norm's
  consensus pool — no wording filter: whatever the wording, if the line was matched
  to that unique SEK norm, it joins the pool.
- One norm (e.g. `направа на тухлен зид`, 25 €/м²) is applied by hundreds of KCC
  lines whose wording ranges very wide — every submitter priced via the same norm.
- **The median over all lines in a pool IS the SEK norm price** — static, valid,
  undeniable: the pool's price distribution converges on the norm itself.
- When the matcher selects **any** pool line as the candidate for the line being
  priced, the **SEK norm price is applied** to the priced line — there is no
  second-guessing which pool candidate won: they all answer the same norm price.
- Pool membership does NOT restrict the matcher: a hooked line stays a normal
  corpus candidate for any KCC line; the hook only pins the price answer.
- Generating the pools doubles as a **passive DB audit**: the same crawl lists
  every norm with its hooked evidence, including norms with thin pools.

Job — consensus pools generation: crawl **all EOP-sourced priced KCC files that
have a SEK-norm counterpart** (the analysis workbooks) in a deterministic way —
extract every sheet, group lines by their `Шифър` SEK code, emit one pool per
unique norm (~46 000 norms → candidates for all).

### Processing notes
- Analysis workbooks are BOTH price-bearing and norm-bearing — extract **every**
  sheet (the all-sheets rule exists for exactly these: thousands of sheets, one
  position each; a silent empty read = a missed norm hook).
- The norm hook (line → sheet → `Шифър`) is written into the row's provenance
  (`source_record` / `origin.note`) so the consensus stays traceable.
- A norm applied as `*` (outside rated range) stays labelled — it prices the line it
  was applied to, but the star is part of the record.
- Runs under the same process rules as everything else here: debugging convention,
  all skills, to-do list, greenlit — and the vocab-training acceptance bar (zero
  `no-recall` lines) covers hooked-norm recall too.

---

## Concepts — the vocabulary you must share

### Median — what it is and how it's formed
The middle value of a sorted list of prices (statistics.median): half the prices are
below it, half above. Used instead of the mean because corpus prices for the same work
spread 2–5× and one outlier can't drag a median. How the evals form it
(`tools/eval_median.py`):
- **Twin found** (same normalized text + compatible unit, from a DIFFERENT tender/source) → price = **median of the twin rows' prices**, each converted through `unit_rate_factor`. This is *observed* evidence.
- **No twin** → take the k nearest same-meaning rows (bge-m3 cosine ≥ sim_min), drop the ones that contradict (unit dimension, declared spec, action family), price = **median of the survivors' converted prices**. This is *derived* — labelled `median`, never dressed as observed.

### Consensus — how it's generated and applied
The commit test in `tools/eval_embed.py`: does the neighbourhood AGREE on a price?
1. Embed the KCC line → nearest corpus rows by meaning (twin tender hidden).
2. Hard gates drop contradicting rows (unit, spec, action).
3. If the best survivor's similarity ≥ SIM_MIN (0.80): form the **band** = survivors within BAND (0.03) similarity of the best.
4. Band's prices → `med` = band median → `agree` = prices within **±15%** of `med`.
5. **Commit only if ≥60% of the band agrees** → price = median of the agreeing set.
   Otherwise → `review`: no price. Consensus is a majority-agreement gate — disagreement
   means review, never a guess.

### The production commit gate (`match_cost_v2`)
The same idea, stricter, in `app/pipeline.py`:
- Pool sorted by (score, active, priority, id).
- **Margin** = score gap to the first candidate at a *materially different* price (>5%).
  Duplicates quoting the same price aren't rivals — they raise confidence.
- **Ambiguous** = a rival on identical footing (score, method rank, spec footing, matched
  tokens) at a different price → evidence can't pick → no price.
- **Diluted** = ≥4 content words in the KCC line that no candidate explains → fragment
  evidence isn't evidence → no price.
- **Vetoes**: unit conflict, spec/material/thickness/op/category conflict, tech conflict.
- Commits only on `exact_code` / `exact_description` / score ≥ HIGH_CONFIDENCE with no
  conflicts, not ambiguous, not diluted. Everything else: review or unpriced.

### Observed vs derived
`twin` price = observed evidence (operator really charged this).
`median` of neighbours = derived (honest estimate, labelled as such).
A query-code row (`KSO 1.1` → `KSO 1.1`) is an anchor only — never spent as price.

---

## "VM update" — what it means and when a DB re-bake is owed

- **A VM update = the full code diff from the last VM update until the current
  local 7200 code state.** Every diff since the last update is reviewed against
  what now runs at `127.0.0.1:7200`.
- **It means a new DB bake** whenever the local 7200 seed/db/corpus — or any
  logic in that regard — was changed/updated since the last update. Seed,
  corpus, source ingestion, consensus pools: changed → re-bake.
- **It happens ONLY from `c:\lab\oneidiottwoidiots`** — the venv, paths, gits
  and DB in that directory are the environment. A VM update does not in any
  way depend on anything under `c:\lab\idiot`: not its venv, not its paths,
  not its gits, not its db.

## PR/commit ledger — what each change fixed and ships (MANDATORY)

Every PR or commit that touches DB sources, the corpus, seeds, pricing
logic, or the tooling around them MUST be listed here with **what it
fixed** and **what it ships**. If a change isn't on this list, it isn't
done — the ledger plus `CHANGELOG.md` entry plus the PR body are the
definition of landed work.

| PR / commit | Fixed | Ships |
|---|---|---|
| [PR #2](https://github.com/xellunexx/matcher/pull/2) | No runbook existed for processing DB sources (the 12 GB heap had no defined flow); no CHANGELOG.md at all; no consensus-pool tooling; no "VM update" definition. | `DB_SOURCES.md` (6-stage pipeline, gates, SEK/KSO schemes, hooked consensus, 2026 medians, vocab-training mandate, VM-update definition, this ledger); `tools/build_consensus_pools.py` (deterministic norm-pool crawler, verified: 69 sek pools / 176 hooked lines / 4,020 unhooked / 0 skipped on 30111+40111); `CHANGELOG.md`. |

Append new rows at the top of this table, one per PR/commit.

## Big-heap rules (12 GB class)

- Heap → `rows.jsonl` → seed JSON. Never heap → DB directly.
- Sort BEFORE extraction (Stage 1 is mandatory, not optional).
- Sample BEFORE full run. Batch with checkpoints; resume from batch id.
- Intermediates in `work/` (gitignored). Only the final seed JSON enters `pricedb/`.
- Time-box passes: many small runs, not one heroic run.

## Sticky reminders

- Retrieval is NOT proof of price compatibility — retrieval narrows the pool; the gates still decide the price.
- Every new seed gets FULL vocab-training: test → verify → confirm → no `no-recall` left.
- Analyses are double-duty: SEK norms pool + hooked line-wording candidates per norm — extract every sheet, never skip "empty" ones.
- An empty sheet is never empty until proven.
- A line with no honest evidence stays **unpriced**. Never a guess.
- Bulgarian and English only.
- `eval_baseline.py --hide-twin` is the honest test.
- Per-line reports in `work/` are the first place to look when a match smells wrong.
- Nothing you started stays running when its job is done.
- If unsure — ask the operator. No assumptions from previous similar cases.
