import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
NEW = r"C:\lab\tenderops\costdb_seed_sek_2026.json"
OLD = r"C:\lab\tenderops\platform\data\demo\costdb_seed_sek_2026.json"
def load(p):
    d = json.load(io.open(p, encoding="utf-8-sig"))
    return d if isinstance(d, list) else d.get("items") or d.get("rows") or []
new, old = load(NEW), load(OLD)
def key(r): return r.get("code") or r.get("id") or ""
new_by, old_by = {key(r): r for r in new}, {key(r): r for r in old}

# union of all field names
allf = sorted(set().union(*(r.keys() for r in old), *(r.keys() for r in new)))
print("all fields:", allf)
for k in old_by:
    o, n = old_by[k], new_by[k]
    for f in allf:
        ov, nv = o.get(f), n.get(f)
        if ov != nv and f not in ("desc", "name"):
            print(f"  FIELD-DIFF {k}.{f}: {str(ov)[:60]} -> {str(nv)[:60]}")
# encoding/format check
print("new[0] keys:", list(new[0].keys()))
print("new[0] sample:", json.dumps(new[0], ensure_ascii=False)[:400])
print("old[0] sample:", json.dumps(old[0], ensure_ascii=False)[:400])
# raw byte-level: any BOM, trailing issues
raw = io.open(NEW, "rb").read()
print("bom:", raw[:3] == b"\xef\xbb\xbf", "| first bytes:", raw[:40])
