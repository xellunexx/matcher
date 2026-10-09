import json, io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

NEW = r"C:\lab\tenderops\costdb_seed_sek_2026.json"
OLD = r"C:\lab\tenderops\platform\data\demo\costdb_seed_sek_2026.json"

def load(p):
    d = json.load(io.open(p, encoding="utf-8-sig"))
    return d if isinstance(d, list) else d.get("items") or d.get("rows") or []

new, old = load(NEW), load(OLD)
print(f"rows: new={len(new)} old={len(old)}")

def key(r):
    return r.get("code") or r.get("id") or r.get("ref") or ""

new_by = {key(r): r for r in new}
old_by = {key(r): r for r in old}
print(f"keys new={len(new_by)} old={len(old_by)}")
missing_in_new = [k for k in old_by if k not in new_by]
added_in_new = [k for k in new_by if k not in old_by]
print(f"codes missing from new: {missing_in_new}")
print(f"codes added in new: {added_in_new}")

# confusable Latin letters inside Cyrillic words / standalone Latin tokens
LAT = re.compile(r"[A-Za-z]")
def lat_in_text(t):
    # Latin letters embedded inside Cyrillic words or standing in for Cyrillic
    return len(re.findall(r"[A-Za-z]", t or ""))

old_lat_total = sum(lat_in_text(str(r.get("desc") or r.get("name") or "")) for r in old)
new_lat_total = sum(lat_in_text(str(r.get("desc") or r.get("name") or "")) for r in new)
print(f"latin chars in descs: old={old_lat_total} new={new_lat_total}")

# per-row diffs on shared keys
price_diff, desc_diff, unit_diff, other = [], [], [], []
for k in old_by:
    if k not in new_by: continue
    o, n = old_by[k], new_by[k]
    od = str(o.get("desc") or o.get("name") or "")
    nd = str(n.get("desc") or n.get("name") or "")
    if od != nd:
        desc_diff.append((k, od[:70], nd[:70]))
    op, np_ = o.get("amount_eur"), n.get("amount_eur")
    if op != np_:
        price_diff.append((k, op, np_))
    ou, nu = o.get("unit"), n.get("unit")
    if ou != nu:
        unit_diff.append((k, ou, nu))
print(f"desc changes: {len(desc_diff)}")
for k, od, nd in desc_diff[:25]:
    print(f"  {k}:")
    print(f"    old: {od}")
    print(f"    new: {nd}")
print(f"price changes: {len(price_diff)} -> {price_diff[:15]}")
print(f"unit changes: {len(unit_diff)} -> {unit_diff[:15]}")

# remaining latin chars in new (sample)
bad = [(key(r), str(r.get('desc') or r.get('name') or '')[:80]) for r in new if lat_in_text(str(r.get('desc') or r.get('name') or ''))]
print(f"new rows still containing latin: {len(bad)}")
for k, d in bad[:15]:
    print(f"  {k}: {d}")
