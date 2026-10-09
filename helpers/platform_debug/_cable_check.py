import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
with open(r"C:\lab\tenderops\price11.txt", encoding="utf-8") as f:
    corpus = json.load(f)
for kw in ["подложк", "кабел"]:
    print(f"--- {kw} ---")
    for c in corpus:
        d = (c.get("desc") or "").lower()
        if kw in d:
            print(f"  {c['id']} | {c['desc'][:70]} | {c.get('unit')} @{c.get('money',{}).get('amount')}")
