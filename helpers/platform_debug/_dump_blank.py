import json

out = json.load(open(r"C:\lab\tenderops\platform\_replay_out.json", encoding="utf-8"))
blank = [r for r in out if r["tier"] == "unmatched"]
print(len(blank))
for r in blank:
    win = str(r.get("suggested_code"))[:26]
    print(f"L{r['line_no']:>4} | {r['desc'][:72]} | win={win} {r.get('confidence')}")
