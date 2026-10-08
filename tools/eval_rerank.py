# -*- coding: utf-8 -*-
"""Hidden-twin eval: bge-m3 shortlist -> LLM "same work?" check -> deterministic price.

1. Meaning retrieval: bge-m3 nearest rows (twin tender hidden), cheap hard gates
   (unit dimension, declared spec conflict, demolish-vs-install).
2. LLM verifier: per line, which shortlisted rows are THE SAME work item
   (same object - not a part/accessory, same operation, same scope, same declared
   material/spec). Output is a few indices -> fast. The LLM never sees prices.
3. Price: confirmed rows only; prefer the company's own prices; commit when the
   confirmed prices agree within 15%, else review. Nothing confirmed -> unpriced.

Run:  python tools/eval_rerank.py --base-url http://127.0.0.1:10011 --model <id>
"""
import argparse, json, re, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

import _paths
HERE = _paths.WORK
import matcher as M  # noqa: E402
from app import llm  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--base-url", required=True)
ap.add_argument("--model", required=True)
ap.add_argument("--api-key-env")
ap.add_argument("--shortlist", type=int, default=8)
ap.add_argument("--batch", type=int, default=6, help="lines per LLM call")
ap.add_argument("--workers", type=int, default=4, help="parallel LLM calls (match llama-server -np)")
ap.add_argument("--sim-min", type=float, default=0.60)
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--reasoning", default="low")
a = ap.parse_args()
import os
API_KEY = os.environ.get(a.api_key_env) if a.api_key_env else None
EURBGN = 1.95583

SYSTEM = """Ти си български сметчик. За всеки ред от КСС ти е даден кратък списък с позиции от
ценова база. Посочи кои позиции са СЪЩАТА РАБОТА като реда, т.е. по тях може да се ползва цената:
- същият обект (не част, аксесоар или принадлежност: „дъска за тоалетна чиния" НЕ е „тоалетна чиния";
  „сифон за мивка" НЕ е „мивка"; „шпакловка" НЕ е „мазилка");
- същата операция (демонтаж ≠ монтаж ≠ демонтаж и обратен монтаж; преработка ≠ ново изграждане);
- същият обхват (доставка и монтаж ≠ само монтаж/труд ≠ само доставка; ред без уточнение в КСС
  обикновено е с труд и материали);
- ако редът посочва материал/клас/размер (AL/Cu, PVC, ф110, 3,2 мм, C25/30), позицията не трябва
  да посочва различен; позиция, която обединява повече неща („ключове и кутии"), не е същата работа
  като единичен ред.
Не гадай. Ако никоя не е същата работа — празен списък.
Отговор: САМО JSON {"r":[{"id":"<ред>","same":[номера]}...]} без обяснения."""

emb = np.load(HERE / "corpus_emb.npy").astype(np.float32)
meta = json.loads((HERE / "corpus_meta.json").read_text(encoding="utf-8"))
lines = json.loads((HERE / "eval_lines.json").read_text(encoding="utf-8"))
if a.limit:
    lines = lines[:: max(1, len(lines) // a.limit)][: a.limit]

t0 = time.time()
enc = SentenceTransformer(_paths.BGE_M3, device="cuda" if __import__("torch").cuda.is_available() else "cpu")
enc.max_seq_length = 128
q = enc.encode([l["text"] for l in lines], batch_size=64, normalize_embeddings=True, convert_to_numpy=True)
t_embed = time.time() - t0


def actions(t):
    return set(M.canonical_tokens(t)) & M._ACTION_CONCEPTS


def gate(line, row):
    f = M.unit_rate_factor(row["unit"], line["unit"])
    if f is None and M.units_compatible(row["unit"], line["unit"]) is not True:
        return None
    if M.spec_conflicts(line["text"], row["desc"]):
        return None
    qa, ca = actions(line["text"]), actions(row["desc"])
    if qa and ca and not (qa & ca):
        return None
    return float(f or 1)


sims_all = q @ emb.T
shortlists = []
for li, line in enumerate(lines):
    sims = sims_all[li]
    seen, keep = set(), []
    for j in np.argsort(-sims)[:200]:
        row = meta[j]
        if row["origin_ref"] == line["hide"] or sims[j] < a.sim_min:
            continue
        key = (row["desc"].strip().lower(), round(row["amount_eur"], 2))
        if key in seen:
            continue
        f = gate(line, row)
        if f is None:
            continue
        seen.add(key)
        keep.append({"sim": float(sims[j]), "eur": row["amount_eur"] * f, "row": row})
        if len(keep) >= a.shortlist:
            break
    shortlists.append(keep)
t_retrieve = time.time() - t0 - t_embed

# dedupe identical (text, unit) lines -> ask once
uniq = {}
for li, line in enumerate(lines):
    uniq.setdefault((line["text"], line["unit"]), []).append(li)
keys = [k for k in uniq if shortlists[uniq[k][0]]]


def ask(chunk):
    payload = []
    for n, k in enumerate(chunk):
        li = uniq[k][0]
        payload.append({"id": str(n), "ред": lines[li]["text"], "мярка": lines[li]["unit"],
                        "раздел": lines[li].get("header") or "",
                        "позиции": {str(i + 1): c["row"]["desc"][:160] for i, c in enumerate(shortlists[li])}})
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    extra = {"chat_template_kwargs": {"reasoning_effort": a.reasoning},
             "response_format": {"type": "json_object"}}
    # an answer is ~15 tokens per line; a hard cap stops runaway repetition loops
    cap = 60 + 30 * len(chunk) if "gpt-oss" not in a.model else 4000
    for attempt in range(2):
        try:
            r = llm.chat(a.base_url, a.model, msgs, api_key=API_KEY, temperature=0, max_tokens=cap,
                         timeout=300, extra=extra)
            txt = r["choices"][0]["message"].get("content") or ""
            m = re.search(r"\{.*\}", txt, re.S)
            got = {str(x.get("id")): x.get("same") or [] for x in json.loads(m.group(0)).get("r", [])}
            return {k: [int(i) for i in got.get(str(n), []) if str(i).isdigit()] for n, k in enumerate(chunk)}
        except Exception as e:
            err = e
    print("  ! batch failed:", err, file=sys.stderr)
    return {k: [] for k in chunk}


t1 = time.time()
verdicts = {}
chunks = [keys[i:i + a.batch] for i in range(0, len(keys), a.batch)]
with ThreadPoolExecutor(a.workers) as ex:
    for done, res in enumerate(ex.map(ask, chunks), 1):
        verdicts.update(res)
        print(f"  llm {done}/{len(chunks)} batches, {time.time() - t1:.0f}s", file=sys.stderr)
t_llm = time.time() - t1

stats = {"lines": len(lines), "committed": 0, "commit_ok": 0, "commit_wrong": 0, "review": 0, "none": 0,
         "confirmed_any": 0, "confirmed_contains_truth": 0,
         # operator rule: twin (same normalized text) -> median of twins, else median of LLM-confirmed rows
         "rule_twin": 0, "rule_twin_ok15": 0, "rule_median": 0, "rule_median_ok15": 0, "rule_median_ok30": 0}
_norm = lambda s: M.normalize_text(s or "")
_by_text = {}
for _j, _r in enumerate(meta):
    _by_text.setdefault(_norm(_r["desc"]), []).append(_j)
report = []
for li, line in enumerate(lines):
    sl = shortlists[li]
    same = [sl[i - 1] for i in verdicts.get((line["text"], line["unit"]), []) if 1 <= i <= len(sl)]
    truth = line["truth"]
    ok = lambda p: abs(p - truth) <= truth * 0.15 or abs(p * EURBGN - truth) <= truth * 0.15
    verdict, price, basis = "none", None, ""
    if same:
        stats["confirmed_any"] += 1
        if any(ok(c["eur"]) for c in same):
            stats["confirmed_contains_truth"] += 1
        own = [c for c in same if c["row"]["origin_kind"] == "operator_pricelist"]
        pool = own or same
        basis = "own" if own else "market"
        prices = [c["eur"] for c in pool]
        med = statistics.median(prices)
        if all(abs(p - med) <= med * 0.15 for p in prices):
            verdict, price = "match", med
        else:
            verdict = "review"
    twins = []
    for j in _by_text.get(_norm(line["text"]), []):
        r = meta[j]
        if r["origin_ref"] != line["hide"]:
            f = gate(line, r)
            if f is not None:
                twins.append(r["amount_eur"] * f)
    if twins:
        stats["rule_twin"] += 1
        stats["rule_twin_ok15"] += ok(statistics.median(twins))
    elif same:
        rp = statistics.median([c["eur"] for c in same])
        stats["rule_median"] += 1
        stats["rule_median_ok15"] += ok(rp)
        stats["rule_median_ok30"] += abs(rp - truth) <= truth * 0.3 or abs(rp * EURBGN - truth) <= truth * 0.3
    if verdict == "match":
        stats["committed"] += 1
        stats["commit_ok" if ok(price) else "commit_wrong"] += 1
    else:
        stats[verdict] += 1
    report.append({"text": line["text"], "unit": line["unit"], "truth": truth, "verdict": verdict,
                   "price": price, "basis": basis,
                   "confirmed": [(round(c["eur"], 2), c["row"]["desc"][:80], c["row"]["origin_kind"]) for c in same],
                   "shortlist": [(round(c["sim"], 3), round(c["eur"], 2), c["row"]["desc"][:80]) for c in sl]})

stats["seconds"] = {"embed": round(t_embed, 1), "retrieve": round(t_retrieve, 1), "llm": round(t_llm, 1),
                    "total": round(time.time() - t0, 1), "llm_calls": len(chunks), "unique_lines": len(keys)}
print(json.dumps(stats, ensure_ascii=False))
print("baseline current matcher (twin hidden): committed 43, within15 9")
out = HERE / f"eval_rerank_{re.sub(r'[^A-Za-z0-9]+', '_', a.model)[-30:]}.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("report ->", out)
