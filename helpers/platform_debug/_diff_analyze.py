import sys, json, re
sys.stdout.reconfigure(encoding="utf-8")
d = json.load(open(r"C:\lab\tenderops\platform\_diff.json", encoding="utf-8"))
# dedupe by ERP line_no
seen = {}
for k, lst in d.items():
    for x in lst:
        seen.setdefault(x["line"], {})[k] = x
# per-line winner category = the most specific bucket containing it
for tag in ["tos_only", "diff_winner", "both_blank"]:
    print(f"\n########## {tag} ##########")
    items = sorted({x["line"]: x for x in d[tag]}.items())
    for ln, x in items:
        print(f"L{ln} {str(x['tos_desc'])[:66]}")
        print(f"   TOS {x['tos_tier']} @{x['tos_price']} <- {x['tos_code'][:26]}")
        print(f"   ERP {x['erp_tier']} @{x['erp_rate']} <- {x['erp_code'][:26]}")
# unique lines per category
print("\n=== unique ERP lines per category ===")
for k, lst in d.items():
    print(f"{k}: {len({x['line'] for x in lst})}")
