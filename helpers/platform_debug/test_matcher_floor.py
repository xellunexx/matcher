# -*- coding: utf-8 -*-
"""Regression probe: the six corpus-mismatch cases from run e8c19e77.

Verifies content-token scoring kills process-word bridges while keeping
real matches alive. Run: venv python test_matcher_floor.py
"""
import sys

sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")

from app.modules.cost_match.matcher import REVIEW_CONFIDENCE, score_match

CASES = [
    # (query, candidate, expect_below_review, label)
    ("Доставка и монтаж на захранващ блок тоководеща шина",
     'Доставка и монтаж на 19" комуникационен шкаф, офисен - OD-L', True, "busbar->cabinet"),
    ("Обучение на персонал за работа със системата",
     "Пералня с капацитет оптимален 15 кг (максимален 18 кг) високооборотна 1000 об/мин инверторна",
     True, "training->washing machine"),
    ("Направа на отвор в стената за монтаж на розетки",
     "Розетки", True, "wall opening->1-word Rozetki"),
    ("Доставка и монтаж на пач панел: 24 - порта",
     "Доставка и монтаж на надлеглови панели", True, "patch panel->lintels"),
    ("Доставка и монтаж на разделителен панел; 1U",
     "Доставка и монтаж на надлеглови панели", True, "divider panel->lintels"),
    ("Доставка и монтаж на вибрационен детектор за каса",
     "Доставка и монтаж на надлеглови панели", True, "detector->lintels"),
    # Near-misses the 0.4 floor over-killed in run 38a3f627: multi-token
    # shared content must not carry the thin_content_coverage cap.
    # Expectation key "no_cap" = thin_content_coverage absent.
    ("Демонтаж на фаянс по стени - санитарни помещения h=2.25 м.",
     "Къртене на фаянс и теракота", "no_cap", "demolish faience->demolish faience"),
    ("Доставка и монтаж тротоарни плочки с фаска повърхност видима",
     "Редене на тротоарни плочки", "no_cap", "paving slabs->paving slabs"),
    ("Топлоизолация по дъна еркери каменна вата 15 см.",
     "Каменна вата Rock LIGHT 30 кг. , 100х600х1200", "no_cap", "stone wool->stone wool"),
    ("Полагане на дъсчена обшивка стрехи (топла връзка)",
     "НАПРАВА НА ДЪСЧЕНА ОБШИВКА ЗА ПОКРИВАНЕ (М2)", "no_cap", "board cladding->board cladding"),
    # Controls: must keep matching (real same-family items)
    ("Доставка и монтаж на 19'' комуникационен шкаф (RACK); 27U",
     'Доставка и монтаж на 19" комуникационен шкаф, офисен - OD-L', False, "27U cabinet->cabinet"),
    ("Доставка и монтаж на комбинирано табло за скрит монтаж",
     "Табло КОМБИНИРАНО за скрит монтаж MSF 2 x 12 + LV 2 с вентилирана метална врата EPN",
     False, "position24->ЕЛ-0854"),
    ("Полагане на гипсова шпакловка по стени",
     "Полагане на гипсова шпакловка по стени и тавани", False, "spackle->spackle"),
    ("Демонтаж на теракотни плочки", "Демонтаж на стари плочки", False, "demolition->demolition"),
]

fails = []
for q, c, expect_low, label in CASES:
    s = score_match(q, c, query_unit="PIECE", candidate_unit="PIECE")
    if expect_low == "no_cap":
        ok = "thin_content_coverage" not in s.reasons
    else:
        ok = (s.confidence < REVIEW_CONFIDENCE) == expect_low
    if not ok:
        fails.append(label)
    print(f"{'OK ' if ok else 'FAIL'} {s.confidence:7} {s.band:6} "
          f"cov={s.factors['query_coverage']:.2f} ovl={s.factors['term_overlap']:.2f} "
          f"{s.reasons}  | {label}")

print(f"\n{len(CASES)-len(fails)}/{len(CASES)} pass" + (f" — FAILURES: {fails}" if fails else ""))
sys.exit(1 if fails else 0)
