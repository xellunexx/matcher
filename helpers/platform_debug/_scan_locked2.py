# -*- coding: utf-8 -*-
"""Targeted extraction from the locked investor UI."""
import re

P = r"app\maybe\tenderops-levelup\tenderops-levelup\tenderops-investor-ui-locked\app.js"
t = open(P, encoding="utf-8").read()

def show(anchor, window=2200):
    i = t.find(anchor)
    print(f"\n===== anchor: {anchor!r} (at {i}) =====")
    if i < 0:
        print("ABSENT")
        return
    print(t[i - 200:i + window])

for a in ["function startProcess", "function bindProcess", "const STAGES", "processStage",
          "function renderReview", "function renderWorkflow", "confirm", "finalReview",
          "function renderHistory", "function renderEstimation", "function renderTenders",
          "function bindUpload", "function showNotification", "/api/tenders/add"]:
    show(a)
