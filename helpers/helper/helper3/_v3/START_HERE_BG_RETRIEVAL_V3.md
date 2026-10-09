# Bulgarian KCC matcher — v3 signature retrieval upgrade

## The point

**The database retrieves by parsed work meaning. The matcher still proves whether a quotation can price the row. No LLM.**

This is the next upgrade to the canonical v2 package, not a competing matcher and not a replacement for its safety gates.

## What to do with each file

| File | Purpose | What you do |
|---|---|---|
| `bulgarian-costmatch-signature-v3.zip` | Changed backend files, tests, both patch options and verification results. | Extract it. Choose one installation route below. |
| `START_HERE_BG_RETRIEVAL_V3.md` | This guide; also inside the ZIP. | Read before applying the upgrade. |
| `kcc2-SIGNATURE-output.xlsx` | Fresh offline KCC2 replay. | Inspect prices and the `BG_MATCH_AUDIT` sheet. Do not import it into the corpus as new operator evidence. |
| `bulgarian-costmatch-v2-to-v3.patch` inside ZIP | Upgrade from the supplied, unchanged v2 code. | Use only if v2 is already installed and you choose the patch route. |
| `bulgarian-costmatch-original-to-v3.patch` inside ZIP | All v1, v2 and v3 changes against your original uploaded backend. | Use only against that original code, not over v1/v2. |
| `verification/RETRIEVAL_V3_RESULTS.json` inside ZIP | Replay, bad-row checks, test verdicts and limits. | Evidence, not application data. |
| `verification/SIGNATURE_RETRIEVAL_PG_RESULTS.json` inside ZIP | Real local PostgreSQL test with all 87,751 uploaded corpus records. | Evidence, not a production database export. |

The enriched corpus supplied in v2 remains useful as a sidecar/audit dataset. **You do not need to re-import it or replace any quotations for this upgrade.** The backend derives current signatures from its existing cost items when needed.

## Install — choose ONE route

First back up the code files you will replace and your database using your normal backup process. Stop the backend while changing its files.

### Route A: v2 already installed, copy files

1. Extract the v3 ZIP into a separate folder.
2. Copy its `app/`, `scripts/` and `tests/` contents over the same relative locations in the backend. These are changed-file overlays; keep all other backend files.
3. Do **not** apply a patch after copying these files.
4. Restart the backend. Restart matters because parser/catalog functions are cached within a running process.

### Route B: v2 already installed, apply the incremental patch instead

Run at the backend root, with the incremental patch placed there:

```powershell
git apply --check .\bulgarian-costmatch-v2-to-v3.patch
if ($LASTEXITCODE -eq 0) { git apply .\bulgarian-costmatch-v2-to-v3.patch }
```

Restart the backend. Do not also copy the overlay files.

If the check reports conflicting local edits, stop and reconcile those edits. Do not force the patch through or silently overwrite work.

### Route C: original uploaded backend, no v1/v2 installed

Use the original-to-v3 patch instead:

```powershell
git apply --check .\bulgarian-costmatch-original-to-v3.patch
if ($LASTEXITCODE -eq 0) { git apply .\bulgarian-costmatch-original-to-v3.patch }
```

Restart. This includes the earlier canonical parser, parent context, provenance and price-safety changes. The v3 overlay alone is NOT enough for the original backend. If you only installed v1, install v2 first, then take Route A or B.

**No schema migration or source-price re-import is required.** Current signatures are stored under the existing `CostItem.metadata["canonical_work"]`. The first Bulgarian retrieval updates missing/stale derived metadata in the selected source/region/catalog scope. The application database role therefore needs permission to update cost-item metadata. Changes participate in the normal session transaction; they are not separately committed by retrieval.

## What was wrong

Previously, retrieval assembled a word pool. Shared generic words, numbers or an early candidate limit could bring unrelated work into the pool or hide relevant quotations. The proposed raw SQL synonym filter would improve that, but would create a second vocabulary separate from the canonical Bulgarian catalog.

Your correction establishes the right boundary: **read the row once into a canonical signature; query quotations using that same signature.**

## What v3 does

For:

```text
Разваляне на 25 см. тухлен зид
```

the shared parser produces:

```text
object     = masonry
operations = [demolish]
materials  = [brick]
length/thickness specification = 0.25 m
```

Retrieval requires the stated concept groups together. Its PostgreSQL containment predicate is equivalent to:

```sql
CAST(metadata -> 'canonical_work' -> 'work' AS jsonb)
    @> '{"object":"masonry","operations":["demolish"],"materials":["brick"]}'::jsonb
```

The actual query also checks signature freshness, active status and existing source/region/catalog restrictions. Source quotations keep their original description, unit, rate, currency, code and provenance.

For `Разваляне на зид`, the requirements are only:

```json
{"object":"masonry","operations":["demolish"]}
```

**No material clause is generated.** It does not assume brick, use a wildcard brick term or turn the groups into OR.

- Synonyms are interpreted by `work_definitions_bg.json` and the shared parser, not independent SQL word lists.
- Missing/stale signatures are rebuilt when their catalog digest, index version, source quotation or parent work context changes.
- Bulgarian retrieval returns the full filtered cohort, not the first `limit` records. An ordinary candidate cap cannot hide a differing equivalent price.
- Vector expansion cannot append unfiltered candidates to the Bulgarian pool.
- Catalog-recognized operation/object/material wording no longer creates different canonical fingerprints solely because of those aliases. Unexplained qualifiers remain in the identity. Equivalent-price checks also examine weaker lexical aliases, rather than hiding a different price behind their lower surface-word score.
- Unknown objects retain the existing verbatim-quotation fallback; that is exact evidence, not a claim that the parser understands that work. Bare fragments such as `Ф25` still require safe parent context.

## Retrieval is NOT proof of price compatibility

The 25 cm specification, unit, work scope, inclusions/exclusions and location are still checked after retrieval. This is intentional: a 12 cm masonry quotation may enter the same object/operation/material pool, but cannot price 25 cm masonry.

`Премахване на мазилка от тухлен зид` describes plaster removal. The parser identifies plaster as the primary object, so it is already excluded from a masonry-signature pool in the tested case. The final `compare_work` object veto remains in place regardless.

Construction and demolition stay distinct. `Изграждане на тухлен зид`, `Направа на тухлена зидария` and `Изграждане на тухлена стена` belong to the construction family, not demolition.

Retrieval does not return a price directly. The outcome is an original compatible quotation or an explicit review/gap/conflict.

## Checks you can run

From the backend root, using your existing project Python environment:

```powershell
python -m pytest --confcutdir=tests/unit tests/unit/cost_match tests/unit/test_cost_match_matcher.py -q
```

The local PostgreSQL regression uses an isolated cluster and skips if its optional diagnostic packages are missing. To run that check as well:

```powershell
python -m pip install pixeltable-pgserver==0.5.1 psycopg2-binary==2.9.13
python -m pytest --confcutdir=tests/unit tests/unit/cost_match/test_retrieval_bg_postgres.py -q
```

Those two packages are for the local integration test, not production matcher dependencies. Existing SQLAlchemy/asyncpg and backend dependencies are still required.

Then start the backend normally and create a **new match run** for KCC2. Do not mistake an old saved run for a fresh result. Inspect the demolition, construction, fan, plaster, duct and filter rows in particular.

## Verified here

- **171 focused tests passed**, including a real PostgreSQL repository regression. Three warnings concern unavailable async pytest configuration options; the integration test executes using `asyncio.run`.
- Targeted Ruff lint passed. Mypy passed for the three canonical/retrieval core modules. The broader existing service/repository/scorer check is not clean; its pre-existing errors are recorded separately, not represented as a full-project type-check pass.
- All **87,751** corpus records loaded into isolated local PostgreSQL. With the existing `operator` + `BG` scope, **3,875** derived signatures rebuilt once, then **zero** rebuilt on the repeat check. Every original quotation, price, unit, currency, source and exported operator provenance remained unchanged.
- Even with `limit=1`, the database returned **5** brick-demolition quotations, **13** brick-construction quotations and **41** duct quotations. These are retrieval counts, not counts of price-compatible evidence.
- Fresh offline replay: **330 exact + 4 compatible = 334 priced; 164 review + 23 unmatched = 187 unresolved; 90 structural/non-work rows.** All 334 v2 prices and totals remain unchanged. All **19 highlighted bad positions remain unpriced**, checked using their original import-row identities rather than repeated visible position numbers.

## Limits and rollback

- This validates local PostgreSQL retrieval and the supplied offline workbook/corpus. **Your deployed ERP, authentication, concurrency and production database have not been run here.**
- The offline replay reads operator quotations across the CSV. Production still applies its existing source/region/catalog restrictions; its candidate counts need not match the offline runner.
- The 334 automatic prices are not independently verified against corrected ground truth. Unknown wording and missing evidence can still require review.
- No production GIN index was added. The measured local retrieval was fast on this dataset; that is not a production performance guarantee. Scoped stale-signature checks still do database work on each retrieval.
- Roll back by restoring the backed-up code and restarting. Derived `canonical_work` metadata does not replace source evidence. Keep the database backup available; do not bulk-delete quotation metadata or re-import old workbooks to undo this change.

**Bottom line: shared, versioned work meaning controls what reaches the matcher. The existing compatibility checks still control whether anything becomes a price.**
