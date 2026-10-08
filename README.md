# KCC → price matcher

Bare-minimum workspace to understand, reproduce, evaluate and work on one problem:

> **Given any Bulgarian КСС (количествено-стойностна сметка — bill of quantities) from a public
> tender, price every line from a corpus of real construction prices — correctly, in minutes.**

Everything here is real: the production matcher code, the full price corpus (19,891 rows),
and real tenders with the operator's own priced answers to grade against.

---

## 1. The problem in one page

A КСС line is free Bulgarian text plus a unit and a quantity:

```
Демонтаж дървена врата 1,00/ 2,20          бр     1
Преработка ел.инсталация                   бр     6
Доставка и монтаж тоалетна чиния           бр     2
Изравнителна замазка по под                м2   190
```

Every tender is written by a different designer in a different style. Lines never repeat
word-for-word across independent tenders (measured: **0–2 %** exact overlap), but the
*vocabulary* of construction work does converge (a few thousand terms).

The price corpus (`pricedb/`) holds prices from won/filed tenders, market price books
(Buildly СМР / materials / machinery / labour), SEK references and the operator's own
past offers. Every row: description, unit, unit price, provenance.

**The job:** for each KCC line find the corpus rows that describe *the same work* and
produce a price. A line with no honest evidence must stay **unpriced** — never a guess.

### Why it is hard

| Failure | Example (KCC line → what the matcher picked) |
|---|---|
| **Scope blindness** — what the price *includes* | `Доставка и монтаж тоалетна чиния` (290) → `Тоалетна чиния` (25, labour only) |
| | `Доставка и монтаж балатум 3,2 мм` (31) → `Монтаж на балатум / мокет` (4) |
| **Object confusion** — which system / which thing | `Преработка Пожароизвестителна инсталация` → `Преработка на ВиК инсталация` |
| | `Доставка и монтаж тоалетна чиния` → `ПВЦ дъска за тоалетна чиния` (accessory, not the object) |
| **Operation confusion** | `Демонтаж на скеле` → `Монтаж и демонтаж на скеле` |
| **Spec blindness** | `бордюри 50х20х10 см` vs `бордюри 8/16`, `15/25` (formats differ, conflict not seen) |
| **Score saturation** | two shared words already give 100/100, so 7 different rows tie at 100 and the ambiguity gate refuses to pick |
| **Price dispersion** | even for *the same work*, corpus prices spread 2–5× (`Шпакловка по гипсокартон`: 3.58 / 4.00 / 6.13, operator charged 8) |

---

## 2. Evaluation method (use this, nothing else)

`kcc/pairs/` holds tenders twice: the **blank** KCC as published and the **same** KCC
priced by the operator. The operator's price per line is the ground truth.

The priced twins are *also* in the corpus (`costdb_seed_user_operator_prices*.json`,
`origin_ref` = the priced file name). So there are two modes:

- **twin present** — the operator priced this exact tender before. Trivial; must be ~100 %.
- **twin hidden** — remove that tender's rows from the corpus and price the blank KCC
  from everything else. **This is the real product: a new tender nobody priced yet.**

331 graded lines across 4 tenders (`tools/kcc_io.py`). A price is "right" within ±15 %
(hidden) of the operator's price. Prices are EUR; BGN converts at 1.95583.

---

## 3. Results so far (twin hidden, 331 lines)

| Approach | priced | within 15 % | within 30 % | time / KCC |
|---|---|---|---|---|
| Production matcher (`app/pipeline.py: match_cost_v2`) | 43 | **10** | — | ~2 min |
| **Twin text** found in another tender/source → median of twins | 21 | **20 (95 %)** | — | ms |
| No twin → **median of 5 nearest rows by meaning** (bge-m3, gated) | 196 | 42 (21 %) | 99 (51 %) | **~10 s** (GPU) |
| No twin → bge-m3 shortlist → BgGPT-12B "same work?" → median | 143 | 39 (27 %) | 73 (51 %) | ~220 s |
| LLM frame parser (`tools/kcc_frame.py`) | wiring verified only — too slow per KCC as designed (parses corpus rows at query time) | | | hours |

With the twin present, the production matcher prices only 117/331 — and 19 of those wrong —
because of a ranking defect, see §5.

**Where it stands:** meaning retrieval (bge-m3) finds the right *kind* of work and is fast.
The remaining error is (a) fine distinctions an embedding blurs (object vs accessory, AL vs
Cu, scope) and (b) price dispersion within the same work. Twin-or-median gives a usable
number for ~half the lines within ±30 %.

---

## 4. Repo layout

```
app/                       production code (stdlib only), copied verbatim from TenderOps
  matcher.py               evidence matcher: normalisation, BG concepts, units, specs, score_match()
  costdb.py                SQLite + FTS5 corpus: import seeds, retrieval_terms, search_candidates()
  pipeline.py              match_cost_v2(): retrieval -> scoring -> commit gate (ambiguity/dilution)
  corpus_aliases.py        curated bill-term aliases per corpus code
  llm.py                   OpenAI-compatible client (llama-server, OpenRouter, ...)
  eop.py validate.py telemetry.py   imported by pipeline.py, not relevant to matching
pricedb/                   the price corpus: costdb_seed*.json (source of truth, 19,891 rows)
kcc/pairs/                 blank KCC + operator-priced twin (see PAIRS in tools/_paths.py)
kcc/unpriced/              real KCCs without answers (for manual testing)
tools/
  _paths.py                repo-relative paths, PAIRS list
  build_db.py              pricedb/*.json -> work/tenderops.sqlite3 (+ FTS index)
  kcc_io.py                KCC reader + work/eval_lines.json (331 graded lines)
  eval_baseline.py         production matcher, --hide-twin for the real case
  embed_corpus.py          bge-m3 embeddings of the corpus -> work/corpus_emb.npy (one-time)
  eval_embed.py            meaning retrieval + gates + price-consensus commit
  eval_median.py           operator rule: twin -> twin price, else median of k nearest
  eval_rerank.py           bge-m3 shortlist -> LLM "same work?" -> price
  kcc_frame.py             LLM frame parser + deterministic frame matcher (experimental)
work/                      generated, gitignored
```

## 5. Known defects in the production matcher

1. **Exact match loses the tie.** `match_cost_v2` sorts candidates by
   `(score, active, priority, id)` (`pipeline.py` ~L1223). The method (`exact_description`)
   is not in the key, so an exact row ties at 100 with loose rows and loses on priority/id
   (`seed_2026-…` > `OPR-…`). The winner is then a loose match → `ambiguous` → unpriced.
   Adding the method rank as the 2nd sort key: twin-present 117 → **283/331**, wrong 19 → 5.
2. **`л.м.` parses as litres.** `matcher._unit_norm("л.м.")` strips dots → `лм` → falls back
   to first word `л` = litre (volume) → unit conflict vs `M` → exact matches blocked (36 lines).
   `costdb._norm_unit("л.м.")` correctly says `M`; the two layers disagree.
3. **Process words are excluded from content** (`_PROCESS_CONCEPTS`), so `доставка и монтаж`
   vs `монтаж` (the scope — biggest price driver) is invisible to the score.

## 6. Quick start

```bash
pip install -r requirements.txt          # torch: install the CUDA build for your GPU first
python tools/build_db.py                 # ~1 min, builds work/tenderops.sqlite3
python tools/kcc_io.py                   # work/eval_lines.json
python tools/eval_baseline.py            # production matcher, twin present
python tools/eval_baseline.py --hide-twin

# meaning retrieval (bge-m3, ~2.3 GB download on first use; or set BGE_M3_PATH to a local copy)
python tools/embed_corpus.py             # one-time, ~45 s GPU / ~20 min CPU
python tools/eval_median.py              # twin-or-median rule, prints a k/threshold grid
python tools/eval_embed.py 0.80 0.03     # consensus commit; add `own` for operator prices only

# LLM verifier (any OpenAI-compatible endpoint), e.g. BgGPT via llama-server:
#   llama-server -m BgGPT-Gemma-3-12B-IT-Q6_K.gguf --port 10011 -c 32768 -np 4 -ngl 99 --jinja
python tools/eval_rerank.py --base-url http://127.0.0.1:10011 --model <id from /v1/models>
```

Every eval writes a per-line JSON report to `work/` (line, truth, chosen price, candidates,
reasons) — that is where to look first.

## 7. Ground rules (from the product)

1. Never fabricate prices, quantities, evidence or provenance.
2. LLM output is advisory; deterministic code makes the final price decision.
3. Missing evidence stays unresolved — no silent guessed price.
4. Keep traceability: KCC line → corpus row(s) → price, and label observed vs derived
   (a median of neighbours is *derived*).
5. Bulgarian KCC pipeline only; Latin technical codes remain specifications.

### Grounded Bulgarian work frames (opt-in)

The old scorer remains the default; its lexical confidence is not proof of compatible
work. `app/workframe.py` validates LLM interpretations and requires agreement on the
object and role, trade, complete operation bundle, explicit scope, material, technical
specifications, included/excluded work and unit dimension. Conflicts reject a source;
unknowns, ambiguities and dropped claims require review. Quotes prove provenance,
not semantic correctness. Conservative operation/numeric-spec checks also flag common
omissions; they are not a proof that every construction requirement was captured.

`pipeline.match_cost_understood(row, db_path, client.frames)` exposes the opt-in path.
Only the query can invoke the model online; corpus frames must already be cached.
There is no fallback to a lexical price. Output includes the query frame, per-candidate
reasons, source IDs and `price_kind`: `observed` or `derived_median`. Compatible prices
with more than 15% dispersion remain unresolved. Model requests contain descriptions,
units and headers, never corpus prices or evaluation answers.

```powershell
python -m unittest discover -s tests -v
python tools/kcc_frame.py --base-url http://127.0.0.1:10011 --model YOUR_MODEL ping
python tools/kcc_frame.py --base-url http://127.0.0.1:10011 --model YOUR_MODEL line "Доставка и монтаж тоалетна чиния" --unit бр --header "ВиК"
python tools/kcc_frame.py --base-url http://127.0.0.1:10011 --model YOUR_MODEL index
python tools/kcc_frame.py --base-url http://127.0.0.1:10011 --model YOUR_MODEL eval --limit-lines 40 --cands 40
```

`index` is resumable, but the first full run interprets roughly 20,000 corpus rows:
test small batches with `index --limit 100` before committing that model cost/time.
Evaluation hides the priced twin and refuses entirely unindexed shortlists. FTS is
the default retriever; `eval --retrieval embedding` uses the existing bge-m3 corpus
index instead. Cache identity includes prompt, endpoint, model, text, unit and header;
changing model weights behind the same name requires clearing/rebuilding the cache.

Verified legacy regression on 331 rows after method-aware ranking and Bulgarian
linear-metre fixes: twin-present 328 priced / 323 correct / 5 wrong; hidden-twin
61 priced / 26 within 15% / 35 wrong. These are **not** work-frame results. Bulgarian
model interpretation accuracy and work-frame price coverage still need live evaluation
before enabling this path by default.

## 8. Models

- **bge-m3** (`BAAI/bge-m3`) — multilingual embeddings, the meaning-retrieval layer.
- **BgGPT-Gemma-3-12B-IT** (INSAIT, `INSAIT-Institute/BgGPT-Gemma-3-12B-IT-GGUF`, Q6_K 9.7 GB) —
  Bulgarian LLM, fits a 16 GB GPU together with bge-m3. Tested as the "same work?" verifier.
- `gpt-oss-20b` was tried and is weak in Bulgarian (rejects obvious equivalents).
