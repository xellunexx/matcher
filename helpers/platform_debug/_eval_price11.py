import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from decimal import Decimal
import openpyxl

# load corpus
with open(r"C:\lab\tenderops\price11.txt", encoding="utf-8") as f:
    corpus = json.load(f)
print("corpus items:", len(corpus))
print("currencies:", {i.get("money",{}).get("currency") for i in corpus[:5000]})

# simple tokenizer for matching
def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^а-яa-z0-9]+", " ", s)
    return [t for t in s.split() if len(t) > 2]

# load zero-price rows from test.xlsx
wb = openpyxl.load_workbook(r"C:\Users\ochak\Downloads\test.xlsx")
ws = wb["BOQ"]
zeros = []
for row in ws.iter_rows(min_row=3, values_only=True):
    pos, desc, unit, qty, rate = row[0], row[1], row[2], row[3], row[4]
    if desc and unit and qty is not None and float(rate or 0) == 0:
        zeros.append((pos, desc, unit, qty))
print("zero-price leaf rows:", len(zeros))

# naive best-match: token overlap on desc
corpus_tok = [(c["id"], c.get("desc",""), c.get("unit"), c.get("money",{}).get("amount"), set(norm(c.get("desc","")))) for c in corpus]
hits = 0
out = []
for pos, desc, unit, qty in zeros:
    q = set(norm(desc))
    if not q:
        continue
    best, best_s = None, 0
    for cid, cdesc, cunit, amt, ct in corpus_tok:
        if not ct:
            continue
        s = len(q & ct) / max(len(q | ct), 1)
        if s > best_s:
            best_s, best = s, (cid, cdesc, cunit, amt)
    if best and best_s > 0.3:
        hits += 1
        out.append((pos, desc, unit, best, round(best_s, 3)))
print(f"matched >0.3 jaccard: {hits}/{len(zeros)}")
for r in out[:25]:
    pos, desc, unit, (cid, cdesc, cunit, amt), s = r
    print(f"  {pos} {desc[:50]} ({unit}) -> {cdesc[:50]} [{cunit} @{amt}] s={s}")
