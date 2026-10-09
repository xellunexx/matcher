# Bulgarian KCC matcher: canonical corpus edition

## The result and its limit

On the supplied 616-row KCC2, using the supplied 87,751-row corpus: **334 rows have an operator quotation**, **187 work rows have no automatic price**, and **90 are headings/non-work**. Four cable quotations become usable through equivalent technical notation. Fifteen former automatic matches on bare `Ф20`, `Ф25`, `Ф32`, etc. are now review-only: the same dimension appears under different work headings (pressure class, pipe type, insulation, compensator). An original set of 14 headings is no longer counted as work. The highlighted wrong object/material/size matches still have no price.

The original price records were not changed. The metadata sidecar contains **all 87,751 original quotations, field-for-field identical**, plus a versioned deterministic work interpretation and its fingerprint. A fingerprint is **not** approval of a price. Only 2,611 of the 3,964 active operator price records have a known broad object family; 1,759 have a scope inferred from their wording. Do not mistake missing interpretations for verified equivalences.

## Where each file goes

- `bulgarian-costmatch-canonical-v2.zip`: complete **ERP backend overlay**, plus tests, replay/audits, this guide, the derived corpus sidecar and replay output. **Use this ZIP if you have either the original ERP bundle or my previous fix.** Copy its `app/`, `scripts/` and `tests/` subtrees over the corresponding ERP backend subtrees. Keep a backup. Copy every included application file, especially `work_definitions_bg.json`; partial copying breaks startup. No database migration or LLM server is involved.
- `bulgarian-costmatch-canonical-v2.patch`: equivalent **code** changes relative to the *original backend ZIP*. Apply this **instead of** copying the v2 overlay, and only if working from that exact original source. The patch does not contain the large corpus sidecar or the workbook.
- `bulgarian-costmatch-v1-to-v2.patch`: equivalent **code** changes relative to the **previous `bulgarian-costmatch-fix.zip`**. Apply this instead of the overlay, and only if that previous version is installed without intervening edits. Never apply both patches or ZIP + patch.
- `data/corpus-bg-canonical.jsonl.gz` inside the ZIP: optional derived corpus export for inspection/import planning. Each JSONL entry contains an unchanged `quotation` and a `canonical_work` interpretation. It does **not** automatically import anything into PostgreSQL and must not replace the price CSV.
- `analysis/kcc2-CANONICAL-output.xlsx`: output workbook with automatic unit/total prices only for accepted work; `BG_MATCH_AUDIT` gives the KCC and quotation identities, inherited work heading, candidate, source code and reasons. An incomplete total is **not a final bid**.
- `analysis/kcc2-190-FULL-CORPUS-AUDIT.csv`: all **190 originally unresolved** rows checked against every active positive-priced *known-family* corpus record with a compatible unit. Diagnoses price conflict, reference-only evidence, representation fixed, incomplete/incompatible evidence, unknown meaning, etc. "No known-family operator price" is not proof that no quotation exists anywhere.
- `analysis/kcc2-CANONICAL-GAPS.csv`: first blocking reason for the **187 presently unresolved work rows**; nearest quote is a lead, not an equivalence decision.

## Verify

From the ERP backend root with its dependencies installed:

```powershell
python -m pytest --confcutdir=tests/unit tests/unit/test_cost_match_matcher.py tests/unit/cost_match -q
python scripts/kcc_match_bg.py --corpus .\cost_items.csv --input .\kcc2-INPUT.xlsx --output .\kcc2-CANONICAL-output.xlsx
```

The offline command expects copies of **your** unpriced input and original corpus in the backend root; change file locations if different. Never replay on the previous priced *output*: the runner intentionally preserves existing human prices. Compare the output and its audit sheet before any real BOQ use. Roll back by restoring the backed-up backend files. The patch alternative can first be checked with `git apply --check <patch-file>` on the corresponding pristine version; resolve local conflicts rather than overwriting local work.

## What changed and why

The original matcher let shared generic words or numbers saturate confidence: a fan could match a shrub, brick demolition could match formwork, and Ø160 could match Ø400. The first fix blocked conflicting objects, work bundles, materials, scope, technical specs and units. But refusal alone is not fluency: corpus `СВТ 3х1,5мм2` and KCC `СВТ3x1,5mm²` meant the same cable, while `Ф25` without its parent heading could wrongly inherit any same-sized price.

Now a **single Bulgarian work catalog** drives both sides. It identifies broad object families, operations, material, scope, typed specifications and residual unrecognized wording. It translates documented notation, not arbitrary synonyms. A dimension-only child row inherits only an *explicit, specific* parent work heading; trade headings and unknown parent meaning cannot invent an operation. The ERP retains the parent text in BOQ match audit metadata and on newly confirmed/manual quotation metadata. It does not append unverified `bill_terms` to scored quotation text. Compatible work and the quotation's rate, unit, currency and provenance remain separate. Conflicting prices for equivalent work stay review-only. Known prices are drawn from original quotes, not generated by a language model or synthetic copies.

The offline results and 163 focused tests support these concrete cases, not universal Bulgarian understanding. The full live PostgreSQL path was **not** exercised on this machine; corpus and workbook have **no independently verified correct-price ground truth**. Review unpriced cases and the 334 suggested prices on a staging ERP before relying on this for a customer offer. More corpus prices/verified vocabulary decisions are needed; reducing the 187 by inventing aliases would recreate the original problem.
