# -*- coding: utf-8 -*-
"""KCC frame parser + frame matcher (standalone test tool).

Turns any Bulgarian КСС line (and any corpus row) into the same structured
"frame" with an LLM, validates every slot against the source text, and matches
frames deterministically. The LLM only reads text; it never sees or picks prices.

Model-agnostic: any OpenAI-compatible endpoint (llama-server, OpenRouter,
OpenAI, Gemini OpenAI-compat, ...). Uses tenderops/app/llm.py as the client.

Usage (run with the tenderops venv python):

  # 0) connection check
  python kcc_frame.py ping --base-url http://127.0.0.1:10000 --model BgGPT-Gemma-3-12B-IT-Q6_K

  # 1) one line, see the frame
  python kcc_frame.py line "Преработка ел.инсталация" --unit бр --header "ЧАСТ ЕЛЕКТРО" ...

  # 2) parse a whole КСС -> frames.jsonl
  python kcc_frame.py parse "..\\kcc\\КСС(56517911).xlsx" --out frames.jsonl ...

  # 3) leave-one-tender-out eval on pricedb/kcc pairs (twin hidden)
  python kcc_frame.py eval --limit-lines 40 --cands 12 ...

Connection flags (all commands):
  --base-url URL       default: tenderops data/demo/settings.json
  --model NAME         default: settings.json
  --api-key-env VAR    read the key from this env var (never printed);
                       default: settings.json key when base-url equals settings
  --batch N            lines per LLM call (default 12)

Frames are cached in frames_cache.sqlite3 next to this file, keyed by
(prompt version, model, text, unit, header) - re-runs and model swaps are cheap
to compare.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
from pathlib import Path

import _paths  # noqa: E402
HERE = _paths.WORK
from app import llm  # noqa: E402  (OpenAI-compatible client, stdlib only)

PROMPT_VERSION = "kccframe-v1"
CACHE_DB = HERE / "frames_cache.sqlite3"
SETTINGS = _paths.ROOT / "settings.json"   # optional {"llm": {"base_url":..,"model":..}} - gitignored
KCC_DIR = _paths.KCC_PAIRS
COST_DB = _paths.DB

# ── Closed vocabularies ─────────────────────────────────────────────────────

TRADES = {
    "earthworks": "земни работи, изкопи, насипи, извозване на земни маси",
    "concrete": "бетон, кофраж, армировка, стоманобетон",
    "masonry": "зидарии, тухла, газобетон, камък",
    "roofing": "покриви, керемиди, покривни конструкции",
    "sheet_metal": "тенекеджийски: улуци, водосточни тръби, обшивки от ламарина",
    "carpentry": "дърводелски: дървени обшивки, паркет, дървени елементи",
    "tiling": "облицовки и настилки с плочки: фаянс, теракот, гранитогрес, гранит",
    "plastering": "мазилки, шпакловки, обръщане на отвори",
    "flooring": "настилки (без плочки): замазки, балатум, ламинат, мокет, епоксидни",
    "painting": "бояджийски: латекс, блажна боя, грунд, фасаген",
    "glazing": "стъкларски",
    "steelwork": "железарски: метални конструкции, парапети, решетки",
    "waterproofing": "хидроизолации",
    "thermal_insulation": "топлоизолации, саниране, EPS/XPS/минерална вата",
    "doors_windows": "дограма, врати, прозорци, первази",
    "drywall": "сухо строителство: гипсокартон, окачени тавани",
    "plumbing": "ВиК: водопровод, канализация, санитарен фаянс, арматура",
    "sanitary_accessories": "аксесоари баня/WC: диспенсъри, четки, огледала, закачалки",
    "hvac": "отопление, вентилация, климатизация",
    "electrical": "силнотокови ел. инсталации, кабели, табла, осветление, контакти, ключове",
    "low_current": "слаботокови: пожароизвестяване, СОТ, видеонаблюдение, СКС, домофони",
    "roads_paving": "пътни: асфалт, бордюри, павета, тротоарни плочи, трошен камък",
    "landscaping": "озеленяване, поливни системи, паркова мебел",
    "demolition": "общо разрушаване/къртене без ясен друг занаят",
    "site": "скеле, временно строителство, почистване, извозване на отпадъци",
    "equipment": "съоръжения и оборудване (детски, спортни, машини)",
    "other": "нищо от горните",
}
OPERATIONS = {
    "new": "нов монтаж/изпълнение/полагане/направа",
    "demolish": "демонтаж/разваляне/къртене/премахване (само премахване)",
    "reinstall": "обратен монтаж на съществуващ елемент",
    "demolish_reinstall": "демонтаж И обратен монтаж в един ред",
    "replace": "подмяна: премахване на старо + монтаж на ново",
    "rework": "преработка/ремонт/възстановяване/корекция на съществуващо",
    "haul": "товарене/извозване/депониране",
    "test": "изпитване/измерване/пускане в експлоатация",
    "rent": "наем (скеле, механизация)",
    "clean": "почистване",
    "unknown": "неясно",
}
SCOPES = {
    "supply_install": "доставка + монтаж/изпълнение (труд и материали)",
    "material": "само доставка на материал/изделие",
    "labour": "само труд/монтаж на доставен материал",
    "machine": "механизация",
    "lump": "паушално/комплект без разбивка",
    "unknown": "неясно",
}
UNIT_DIMS = ("area", "length", "volume", "mass", "count", "time", "lump", "unknown")
SPEC_KINDS = ("thickness_mm", "diameter_mm", "size_cm", "concrete_class", "fraction_mm",
              "power_kw", "cross_section_mm2", "height_m", "fire_rating", "other")

# Bulgarian norm chapters (structure of УСН/СЕК, as used by the БЛ01-БЛ25 corpus codes).
NORM_CHAPTERS = {
    "01": "Земни работи", "02": "Кофражни работи", "03": "Армировъчни работи",
    "04": "Бетонови работи", "05": "Зидарски работи", "06": "Покривни работи",
    "07": "Тенекеджийски работи", "08": "Дърводелски работи",
    "09": "Облицовъчни работи", "10": "Мазачески работи", "11": "Настилки",
    "12": "Стъкларски работи", "13": "Бояджийски работи", "14": "Железарски работи",
    "15": "Хидроизолации", "16": "Топлоизолации", "17": "Дограма и столарски работи",
    "18": "ОВК и отопление", "19": "Сухо строителство", "20": "ВиК инсталации в сгради",
    "21": "Външни ВиК мрежи и пътни възстановявания", "22": "Пътни работи и озеленяване",
    "23": "Укрепителни и хидротехнически", "24": "Електрически инсталации",
    "25": "Разрушителни и демонтажни работи", "00": "Друго/извън нормите",
}

# ── Prompt ──────────────────────────────────────────────────────────────────

SYSTEM = f"""Ти си български сметчик-нормировчик. Разбираш КСС (количествено-стойностни сметки) и
работиш по логиката на българските сметни норми (УСН/СЕК). Задачата ти е да превърнеш всеки ред
в структурирана рамка (frame). НЕ определяш цени. НЕ измисляш факти.

Правила:
1. Всяко поле, което попълваш, трябва да има основание в ТЕКСТА на реда или в заглавието на
   раздела (header). Цитирай дословно в "ev" (кратък фрагмент, копиран от текста). Ако няма
   основание — стойност "unknown"/празно.
2. "scope_basis": "explicit" ако редът казва изрично (доставка, монтаж, труд, вкл. материали);
   "norm" ако следва от практиката на КСС: ред без глагол за доставка/труд в тръжна КСС се
   оферира с труд и материали (supply_install). Монтаж/обратен монтаж/полагане на доставен -> labour.
   „Доставка на X" без монтаж -> material.
3. "trade" и "norm_chapter" се определят от обекта на работата И от заглавието на раздела.
   „Преработка инсталация" под „ЧАСТ ЕЛЕКТРО" е electrical; без заглавие и без указание -> unknown.
4. "object": каноничното наименование на това, ВЪРХУ което се работи — лема, именителен падеж,
   единствено число, малки букви, без размери/цвят/марка. Функционалните определения остават
   („тоалетна чиния", „саморазливна замазка", „пожароизвестителна инсталация").
5. "operation": какво се прави. „Монтаж и демонтаж на скеле" е new (наемане/поставяне на скеле),
   „Демонтаж на скеле" сам е demolish. „Подмяна" е replace.
6. "specs": само стойности, написани в текста; нормализирай: дебелина/диаметър в мм (число),
   размери в см като "50x20x10", бетон "C25/30", фракция "20-40", мощност в kW.
7. "includes": допълнителни работи, изрично включени („вкл. грунд" -> "грундиране",
   „вкл. ръбохранители, шпакловка" -> "ръбохранители", "шпакловане").
8. "material": основният материал, ако е написан (латекс, гранитогрес, PVC, битум...).
9. Мерна единица -> "unit_dim": м2=area, м/л.м./м'=length, м3=volume, кг/т=mass,
   бр/компл=count, ч=time, „паушал"/„к-т" без разбивка може да е lump.

Затворени списъци:
trade: {json.dumps(TRADES, ensure_ascii=False)}
operation: {json.dumps(OPERATIONS, ensure_ascii=False)}
scope: {json.dumps(SCOPES, ensure_ascii=False)}
norm_chapter: {json.dumps(NORM_CHAPTERS, ensure_ascii=False)}
spec kind: {list(SPEC_KINDS)}
unit_dim: {list(UNIT_DIMS)}

Изход: САМО JSON обект {{"items":[...]}} — по един елемент за всеки вход, в същия ред, с неговото "id".
Формат на елемент:
{{"id":"..","trade":"..","trade_ev":"..","norm_chapter":"..","operation":"..","op_ev":"..",
 "scope":"..","scope_basis":"explicit|norm|unknown","scope_ev":"..",
 "object":"..","object_ev":"..","material":[".."],"specs":[{{"kind":"..","value":"..","ev":".."}}],
 "includes":[".."],"unit_dim":".."}}
"""

FEWSHOT_IN = [
    {"id": "a", "text": "Преработка ел.инсталация", "unit": "бр", "header": "ЧАСТ ЕЛЕКТРО"},
    {"id": "b", "text": "Демонтаж на скеле", "unit": "м2", "header": ""},
    {"id": "c", "text": "Доставка и монтаж тоалетна чиния", "unit": "бр", "header": "Санитарен възел"},
    {"id": "d", "text": "Монтаж на балатум / мокет", "unit": "м2", "header": ""},
    {"id": "e", "text": "Доставка и полагане на бетонови бордюри 50х20х10 см", "unit": "м'", "header": ""},
]
FEWSHOT_OUT = {"items": [
    {"id": "a", "trade": "electrical", "trade_ev": "ел.инсталация", "norm_chapter": "24",
     "operation": "rework", "op_ev": "Преработка", "scope": "supply_install", "scope_basis": "norm",
     "scope_ev": "", "object": "електрическа инсталация", "object_ev": "ел.инсталация",
     "material": [], "specs": [], "includes": [], "unit_dim": "count"},
    {"id": "b", "trade": "site", "trade_ev": "скеле", "norm_chapter": "00", "operation": "demolish",
     "op_ev": "Демонтаж", "scope": "labour", "scope_basis": "explicit", "scope_ev": "Демонтаж",
     "object": "скеле", "object_ev": "скеле", "material": [], "specs": [], "includes": [],
     "unit_dim": "area"},
    {"id": "c", "trade": "plumbing", "trade_ev": "тоалетна чиния", "norm_chapter": "20",
     "operation": "new", "op_ev": "монтаж", "scope": "supply_install", "scope_basis": "explicit",
     "scope_ev": "Доставка и монтаж", "object": "тоалетна чиния", "object_ev": "тоалетна чиния",
     "material": [], "specs": [], "includes": [], "unit_dim": "count"},
    {"id": "d", "trade": "flooring", "trade_ev": "балатум", "norm_chapter": "11", "operation": "new",
     "op_ev": "Монтаж", "scope": "labour", "scope_basis": "explicit", "scope_ev": "Монтаж",
     "object": "балатум", "object_ev": "балатум", "material": [], "specs": [], "includes": [],
     "unit_dim": "area"},
    {"id": "e", "trade": "roads_paving", "trade_ev": "бордюри", "norm_chapter": "22",
     "operation": "new", "op_ev": "полагане", "scope": "supply_install", "scope_basis": "explicit",
     "scope_ev": "Доставка и полагане", "object": "бордюр", "object_ev": "бордюри",
     "material": ["бетон"], "specs": [{"kind": "size_cm", "value": "50x20x10", "ev": "50х20х10 см"}],
     "includes": [], "unit_dim": "length"},
]}

# ── LLM call + cache ────────────────────────────────────────────────────────


def _settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")).get("llm") or {}
    except Exception:
        return {}


class Client:
    def __init__(self, base_url=None, model=None, api_key_env=None, batch=12, timeout=300):
        s = _settings()
        self.base_url = base_url or s.get("base_url")
        self.model = model or s.get("model")
        if api_key_env:
            self.api_key = os.environ.get(api_key_env) or None
            if not self.api_key:
                sys.exit(f"env var {api_key_env} is empty")
        else:
            self.api_key = s.get("api_key") if self.base_url == s.get("base_url") else None
        self.batch = batch
        self.timeout = timeout
        self.json_mode = True
        self.calls = 0
        self.seconds = 0.0
        self.db = sqlite3.connect(CACHE_DB)
        self.db.execute("CREATE TABLE IF NOT EXISTS frames(k TEXT PRIMARY KEY, model TEXT, frame TEXT, at TEXT)")

    def _key(self, it):
        raw = json.dumps([PROMPT_VERSION, self.model, it.get("text"), it.get("unit") or "",
                          it.get("header") or ""], ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _ask(self, items):
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"lines": FEWSHOT_IN}, ensure_ascii=False)},
                {"role": "assistant", "content": json.dumps(FEWSHOT_OUT, ensure_ascii=False)},
                {"role": "user", "content": json.dumps({"lines": items}, ensure_ascii=False)}]
        extra = {"response_format": {"type": "json_object"}} if self.json_mode else None
        t0 = time.time()
        try:
            r = llm.chat(self.base_url, self.model, msgs, api_key=self.api_key, temperature=0,
                         max_tokens=400 * len(items) + 200, timeout=self.timeout, extra=extra)
        except urllib.error.HTTPError as e:
            if self.json_mode and e.code == 400:
                self.json_mode = False
                return self._ask(items)
            raise
        self.calls += 1
        self.seconds += time.time() - t0
        txt = r["choices"][0]["message"].get("content") or ""
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            raise ValueError(f"no JSON in model output: {txt[:300]}")
        return {str(x.get("id")): x for x in (json.loads(m.group(0)).get("items") or [])}

    def frames(self, items, progress=False):
        """items: [{text, unit, header}] -> list of validated frames (same order)."""
        out = [None] * len(items)
        todo = []
        for i, it in enumerate(items):
            row = self.db.execute("SELECT frame FROM frames WHERE k=?", (self._key(it),)).fetchone()
            if row:
                out[i] = validate(json.loads(row[0]), it)
            else:
                todo.append(i)
        uniq = {}
        for i in todo:
            uniq.setdefault(self._key(items[i]), []).append(i)
        keys = list(uniq)
        for b in range(0, len(keys), self.batch):
            chunk = keys[b:b + self.batch]
            req = [{"id": str(n), "text": items[uniq[k][0]]["text"], "unit": items[uniq[k][0]].get("unit") or "",
                    "header": items[uniq[k][0]].get("header") or ""} for n, k in enumerate(chunk)]
            got = {}
            for attempt in range(2):
                try:
                    got = self._ask(req)
                    break
                except (ValueError, json.JSONDecodeError) as e:
                    if attempt:
                        print(f"  ! batch failed twice: {e}", file=sys.stderr)
            for n, k in enumerate(chunk):
                raw = got.get(str(n))
                if raw:  # cache the model's raw claim; validation reruns on every read
                    self.db.execute("INSERT OR REPLACE INTO frames VALUES(?,?,?,datetime('now'))",
                                    (k, self.model, json.dumps(raw, ensure_ascii=False)))
                for i in uniq[k]:
                    out[i] = validate(raw or {}, items[i])
            self.db.commit()
            if progress:
                print(f"  llm {min(b + self.batch, len(keys))}/{len(keys)} new frames "
                      f"({self.calls} calls, {self.seconds:.0f}s)", file=sys.stderr)
        return out


# ── Grounding validation ────────────────────────────────────────────────────


def _fold(s):
    s = (s or "").lower().replace("х", "x").replace("×", "x").replace(",", ".")
    return re.sub(r"[\s\.\-'’`\"/]+", "", s)


def _fold_val(s):
    """Spec value fold: keeps the decimal point (3,2 -> 3.2), drops units/spaces."""
    s = (s or "").lower().replace("х", "x").replace("×", "x").replace(",", ".")
    s = re.sub(r"(мм|mm|см|cm|kw|квт)$", "", re.sub(r"\s+", "", s))
    return re.sub(r"(?<=\d)\.0+(?!\d)", "", s).strip(".")


def _grounded(ev, src):
    ev = _fold(ev)
    return bool(ev) and ev in _fold((src.get("text") or "") + " " + (src.get("header") or ""))


def validate(fr, src):
    """Every evidence-bearing slot must quote the source; otherwise it becomes unknown.
    Records which slots were dropped so the audit shows what the model claimed."""
    dropped = []
    out = {"text": src.get("text"), "unit": src.get("unit"), "header": src.get("header") or ""}

    def pick(val, allowed, ev_key, field, allow_norm=False):
        v = val if val in allowed else "unknown"
        if v in ("unknown", "other"):
            return v
        if fr.get(ev_key) and _grounded(fr.get(ev_key), src):
            return v
        if allow_norm:
            return v
        dropped.append(field)
        return "unknown"

    out["trade"] = pick(fr.get("trade"), TRADES, "trade_ev", "trade")
    out["operation"] = pick(fr.get("operation"), OPERATIONS, "op_ev", "operation")
    basis = fr.get("scope_basis") if fr.get("scope_basis") in ("explicit", "norm") else "unknown"
    if basis == "explicit" and not _grounded(fr.get("scope_ev"), src):
        basis = "norm"  # claimed explicit, not quoted -> demote to inferred, never to observed
        dropped.append("scope_explicit")
    out["scope"] = fr.get("scope") if fr.get("scope") in SCOPES else "unknown"
    out["scope_basis"] = basis if out["scope"] != "unknown" else "unknown"
    obj = (fr.get("object") or "").strip().lower()
    if obj and not _grounded(fr.get("object_ev"), src):
        dropped.append("object")
        obj = ""
    out["object"] = obj
    out["norm_chapter"] = fr.get("norm_chapter") if fr.get("norm_chapter") in NORM_CHAPTERS else "00"
    out["material"] = sorted({m.strip().lower() for m in (fr.get("material") or []) if isinstance(m, str) and m.strip()})
    specs = []
    for sp in fr.get("specs") or []:
        if not isinstance(sp, dict) or sp.get("kind") not in SPEC_KINDS:
            continue
        if not _grounded(sp.get("ev"), src):
            dropped.append("spec")
            continue
        specs.append({"kind": sp["kind"], "value": _fold_val(str(sp.get("value") or ""))})
    out["specs"] = specs
    out["includes"] = sorted({x.strip().lower() for x in (fr.get("includes") or []) if isinstance(x, str) and x.strip()})
    out["unit_dim"] = fr.get("unit_dim") if fr.get("unit_dim") in UNIT_DIMS else "unknown"
    out["dropped"] = dropped
    return out


# ── Deterministic frame matcher ─────────────────────────────────────────────


def _words(s):
    return [w for w in re.findall(r"\w+", (s or "").lower()) if len(w) > 2]


def _eq(a, b):
    if a == b:
        return True
    lo, hi = sorted((a, b), key=len)
    return len(lo) >= 5 and hi.startswith(lo[:max(5, len(lo) - 2)])


def object_rel(q, c, q_mat=(), c_mat=()):
    """'same' | 'partial' (qualified form, or one side names the material the other
    side's element is made of: 'теракот' vs 'подова настилка' of теракот) |
    'different' | 'unknown'."""
    if not q or not c:
        return "unknown"
    qw, cw = _words(q), _words(c)
    qm = sum(any(_eq(a, b) for b in cw) for a in qw)
    cm = sum(any(_eq(b, a) for a in qw) for b in cw)
    if qm == len(qw) and cm == len(cw):
        return "same"
    if qm == len(qw) or cm == len(cw):
        return "partial"
    cwm = cw + [w for m in c_mat for w in _words(m)]
    qwm = qw + [w for m in q_mat for w in _words(m)]
    if all(any(_eq(a, b) for b in cwm) for a in qw) or all(any(_eq(b, a) for a in qwm) for b in cw):
        return "partial"
    return "different"


_SCOPE_OK = {("supply_install", "supply_install"), ("material", "material"), ("labour", "labour"),
             ("machine", "machine"), ("lump", "lump")}


def compare(q, c):
    """Return (verdict, score, reasons). verdict: match | review | reject."""
    reasons = []
    if q["trade"] not in ("unknown", "other") and c["trade"] not in ("unknown", "other") and q["trade"] != c["trade"]:
        return "reject", 0, [f"trade {q['trade']}≠{c['trade']}"]
    if q["operation"] != "unknown" and c["operation"] != "unknown" and q["operation"] != c["operation"]:
        return "reject", 0, [f"operation {q['operation']}≠{c['operation']}"]
    if q["unit_dim"] not in ("unknown",) and c["unit_dim"] not in ("unknown",) and q["unit_dim"] != c["unit_dim"]:
        return "reject", 0, [f"unit {q['unit_dim']}≠{c['unit_dim']}"]
    orel = object_rel(q["object"], c["object"], q["material"], c["material"])
    if orel == "different":
        return "reject", 0, [f"object '{q['object']}'≠'{c['object']}'"]
    sc = (q["scope"], c["scope"])
    if "unknown" not in sc and sc not in _SCOPE_OK:
        both_explicit = q["scope_basis"] == "explicit" and c["scope_basis"] == "explicit"
        if both_explicit:
            return "reject", 0, [f"scope {sc[0]}≠{sc[1]}"]
        reasons.append(f"scope? {sc[0]}/{sc[1]} (inferred)")
    qs = {}
    for s in q["specs"]:
        qs.setdefault(s["kind"], set()).add(s["value"])
    cs = {}
    for s in c["specs"]:
        cs.setdefault(s["kind"], set()).add(s["value"])
    spec_conf = [k for k in qs if k in cs and not (qs[k] & cs[k])]
    spec_agree = [k for k in qs if k in cs and (qs[k] & cs[k])]
    if spec_conf:
        return "review", 40, [f"spec {k} {sorted(qs[k])}≠{sorted(cs[k])}" for k in spec_conf]

    score = 50
    score += 20 if orel == "same" else 8 if orel == "partial" else 0
    score += 10 if q["trade"] == c["trade"] and q["trade"] not in ("unknown", "other") else 0
    score += 8 if q["operation"] == c["operation"] and q["operation"] != "unknown" else 0
    score += 6 if sc in _SCOPE_OK else 0
    score += 4 * len(spec_agree)
    qm, cm = set(q["material"]), set(c["material"])
    if qm and cm:
        if any(_eq(w, v) for a in qm for b in cm for w in _words(a) for v in _words(b)):
            score += 4
        else:
            score -= 10
            reasons.append(f"material {sorted(qm)}≠{sorted(cm)}")
    elif qm and not any(_eq(w, v) for a in qm for w in _words(a) for v in _words(c["text"] or "")):
        reasons.append("candidate silent on material")
    missing = [x for x in q["includes"] if not any(_eq(w, v) for v in c["includes"] for w in [x])]
    if missing:
        score -= 4 * len(missing)
        reasons.append(f"not included: {missing}")
    if qs and not cs:
        reasons.append("candidate has no spec")
        score -= 4
    if orel == "partial":
        reasons.append(f"object variant '{q['object']}'~'{c['object']}'")
    if orel == "unknown":
        reasons.append("object unknown")
    # Silence is never a conflict, but it is never evidence either: a line that
    # declares a material/spec cannot COMMIT on a row that says nothing about it.
    blockers = ("scope?", "candidate has no spec", "candidate silent on material", "material ")
    verdict = "match" if (orel == "same" and score >= 80
                          and not any(r.startswith(blockers) for r in reasons)) else "review"
    return verdict, min(score, 100), reasons


# ── КСС file reading ────────────────────────────────────────────────────────


def read_kcc(path):
    """Rows with description + unit + numeric qty; the last text-only row above is the header."""
    path = str(path)
    if path.lower().endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
    elif path.lower().endswith(".xls"):
        import xlrd
        sh = xlrd.open_workbook(path).sheets()[0]
        grid = [sh.row_values(i) for i in range(sh.nrows)]
    else:
        sys.exit("only .xls/.xlsx supported by this test tool")
    rows, header = [], ""
    for i, r in enumerate(grid):
        r = (list(r) + [None] * 6)[:6]
        desc, unit, qty, price = r[1], r[2], r[3], r[4]
        text = desc.strip() if isinstance(desc, str) else ""
        u = str(unit).strip() if unit not in (None, "") else ""
        if text and u and len(u) <= 14 and isinstance(qty, (int, float)) and not isinstance(qty, bool):
            rows.append({"i": i, "text": text, "unit": u, "header": header,
                         "price": price if isinstance(price, (int, float)) and price > 0 else None})
        elif text and not u and len(text) < 160:
            header = text
    return rows


# ── Commands ────────────────────────────────────────────────────────────────


def cmd_ping(cl, a):
    ok, note, resolved = llm.test_conn(cl.base_url, cl.model, cl.api_key)
    print(("OK " if ok else "FAIL ") + note)
    if resolved and resolved != cl.model:
        print(f"use --model \"{resolved}\"")


def cmd_line(cl, a):
    fr = cl.frames([{"text": a.text, "unit": a.unit, "header": a.header or ""}])[0]
    print(json.dumps(fr, ensure_ascii=False, indent=1))


def cmd_parse(cl, a):
    rows = read_kcc(a.file)[: a.limit or None]
    frs = cl.frames(rows, progress=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r, fr in zip(rows, frs):
            f.write(json.dumps({"row": r["i"], **fr}, ensure_ascii=False) + "\n")
    unk = sum(1 for fr in frs if fr["trade"] == "unknown" or not fr["object"])
    print(f"{len(frs)} frames -> {a.out} | trade/object unknown: {unk} | calls {cl.calls}, {cl.seconds:.0f}s")


PAIRS = [
    ("КСС(56517911).xlsx", "КСС_НЧ_Своге_КОЛЕВ ГРУП СТРОЙ 2001~(56903555).xlsx"),
    ("КСС(40023554).xlsx", "КСС(40023554)(40500727).xlsx"),
    ("КСС към ценово предложение(57123673).xls", "КСС към ценово предложение(57123673)(57409050).xls"),
    ("4. КСС оферта -2026(56440729).xls", "4. КСС оферта -2026(56720082).xls"),
]

_CAT_CH = re.compile(r"bl(\d\d)", re.I)


def _cand_item(r):
    cat = r.get("category") or ""
    m = _CAT_CH.search(cat) or re.match(r"(?:БЛ|СЕК)(\d\d)", r.get("code") or "")
    hdr = NORM_CHAPTERS.get(m.group(1), "") if m else ""
    return {"text": r.get("desc") or r.get("name") or "", "unit": r.get("unit") or "", "header": hdr}


def cmd_eval(cl, a):
    from app import costdb, pipeline
    lines = []
    for blank, priced in PAIRS:
        truth = {r["i"]: r["price"] for r in read_kcc(KCC_DIR / priced)}
        for r in read_kcc(KCC_DIR / blank):
            if truth.get(r["i"]):
                lines.append({**r, "truth": truth[r["i"]], "hide": priced, "kcc": blank})
    if a.limit_lines:
        step = max(1, len(lines) // a.limit_lines)
        lines = lines[::step][: a.limit_lines]
    print(f"eval lines: {len(lines)} (twin hidden per tender), cands/line: {a.cands}", file=sys.stderr)

    qframes = cl.frames(lines, progress=True)
    pools = []
    for ln in lines:
        raw = costdb.search_candidates(str(COST_DB), ln["text"], ln["unit"], limit=a.cands * 3, include_pending=True)
        raw = [r for r in raw if r.get("origin_ref") != ln["hide"] and r.get("status") != "retired"][: a.cands]
        pools.append(raw)
    allc = [_cand_item(r) for p in pools for r in p]
    cframes = iter(cl.frames(allc, progress=True))

    res = {"lines": len(lines), "committed": 0, "commit_ok": 0, "commit_wrong": 0,
           "review": 0, "none": 0, "best_within15": 0}
    report = []
    for ln, qf, pool in zip(lines, qframes, pools):
        scored = []
        for r in pool:
            cf = next(cframes)
            v, s, why = compare(qf, cf)
            if v == "reject":
                continue
            view = pipeline._cost_row_view(r)
            conv = pipeline._apply_unit_conversion(view, ln["unit"])
            if conv is None:
                continue
            scored.append((s, v, conv["unitEur"], r["id"], r.get("desc"), why, cf))
        scored.sort(key=lambda x: -x[0])
        truth = ln["truth"]
        ok = lambda p: abs(p - truth) <= truth * 0.15 or abs(p * 1.95583 - truth) <= truth * 0.15
        verdict, price = "none", None
        if scored:
            top = scored[0][0]
            full = [x for x in scored if x[1] == "match" and x[0] == top]
            if full:
                prices = [x[2] for x in full]
                spread = (max(prices) - min(prices)) / max(min(prices), 1e-9)
                if spread <= 0.15:
                    verdict, price = "match", statistics.median(prices)
                else:
                    verdict = "review"
            else:
                verdict = "review"
            if ok(scored[0][2]):
                res["best_within15"] += 1
        if verdict == "match":
            res["committed"] += 1
            res["commit_ok" if ok(price) else "commit_wrong"] += 1
        else:
            res[verdict] += 1
        report.append({"kcc": ln["kcc"][:25], "text": ln["text"], "unit": ln["unit"], "header": ln["header"],
                       "truth": truth, "verdict": verdict, "price": price, "qframe": qf,
                       "top": [{"score": x[0], "v": x[1], "eur": round(x[2], 2), "id": x[3], "desc": (x[4] or "")[:90],
                                "why": x[5], "cframe": {k: x[6][k] for k in ("trade", "operation", "scope", "object", "specs")}}
                               for x in scored[:4]]})
    out = HERE / f"eval_{re.sub(r'[^A-Za-z0-9]+', '_', str(cl.model))[-40:]}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False))
    print(f"baseline (current matcher, twin hidden): 331 lines, 43 committed, 9 within 15%")
    print(f"report -> {out} | llm calls {cl.calls}, {cl.seconds:.0f}s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    ap.add_argument("--api-key-env")
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=300)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    p = sub.add_parser("line")
    p.add_argument("text")
    p.add_argument("--unit", default="")
    p.add_argument("--header", default="")
    p = sub.add_parser("parse")
    p.add_argument("file")
    p.add_argument("--out", default="frames.jsonl")
    p.add_argument("--limit", type=int, default=0)
    p = sub.add_parser("eval")
    p.add_argument("--limit-lines", type=int, default=0)
    p.add_argument("--cands", type=int, default=12)
    a = ap.parse_args()
    cl = Client(a.base_url, a.model, a.api_key_env, a.batch, a.timeout)
    {"ping": cmd_ping, "line": cmd_line, "parse": cmd_parse, "eval": cmd_eval}[a.cmd](cl, a)


if __name__ == "__main__":
    main()
