# Diff: ERP live pipeline (_replay_full.json) vs tenderops (kcc2_priced.xlsx)
import os, sys, json, re, asyncio
os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@127.0.0.1:49188/postgres"
sys.path.insert(0, r"C:\lab\tenderops\platform\erp\backend")
sys.stdout.reconfigure(encoding="utf-8")
import openpyxl
from sqlalchemy import text
from app.database import async_session_factory

RUN_ID = "c6bc0407-f256-402b-99a6-a38ca8d3ce65"


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"_x000d_|[^0-9а-яa-z]+", " ", s)
    return " ".join(s.split())[:80]


async def main():
    # ERP side: replay results joined to full DB descriptions
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "select line_no, source_description, source_unit, source_quantity, "
            "suggested_rate from oe_cost_match_result where run_id=:r order by line_no"),
            {"r": RUN_ID})).fetchall()
    erp = json.load(open(r"C:\lab\tenderops\platform\_replay_full.json", encoding="utf-8"))
    desc_by_ln = {r[0]: r[1] for r in rows}
    erp_by_norm = {}
    for r in erp:
        d = desc_by_ln.get(r["line_no"]) or r["desc"]
        erp_by_norm[norm(d)] = r
    print(f"erp lines: {len(erp)}, keyed {len(erp_by_norm)}")

    # tenderops side: KSS sheet rows 2..N
    wb = openpyxl.load_workbook(r"C:\Users\ochak\Music\tendererp\kcc2_priced.xlsx", read_only=True)
    ws = wb["КСС"]
    tos = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None and row[1] is None:
            continue
        tos.append({
            "pos": row[0], "desc": row[1], "unit": row[2], "qty": row[3],
            "price": row[5], "total": row[6], "conf": row[7],
            "tier": row[8], "code": row[9], "cdesc": row[10],
        })
    wb.close()
    print(f"tos rows: {len(tos)}")

    matched, unkeyed = [], []
    for t in tos:
        key = norm(t["desc"])
        e = erp_by_norm.get(key)
        if e is None:
            unkeyed.append(t)
            continue
        matched.append((t, e))
    print(f"aligned {len(matched)}, tenderops-only {len(unkeyed)}")

    cat = {"both": [], "erp_only": [], "tos_only": [], "both_blank": [], "diff_winner": []}
    for t, e in matched:
        t_priced = t["price"] not in (None, "", 0, "0")
        e_priced = e["tier"] != "unmatched" and e.get("suggested_rate") not in (None, "", "0")
        ecode = (e.get("suggested_code") or "").replace("BUILDLY-SMR-", "").replace("BUILDLY-MATERIAL-", "").replace("BUILDLY-LABOR-", "")
        tcode = str(t["code"] or "")
        if t_priced and e_priced:
            tr, er = str(t["price"]), str(e.get("suggested_rate") or "")
            try:
                same = abs(float(tr) - float(er)) / max(1e-9, abs(float(tr))) < 0.01
            except ValueError:
                same = tr == er
            (cat["both"] if same and tcode.split("-")[-1] in ecode else cat["diff_winner"]).append((t, e, ecode))
        elif e_priced and not t_priced:
            cat["erp_only"].append((t, e, ecode))
        elif t_priced and not e_priced:
            cat["tos_only"].append((t, e, ecode))
        else:
            cat["both_blank"].append((t, e, ecode))

    print(f"\n=== same winner+price: {len(cat['both'])}")
    print(f"=== different winner/price: {len(cat['diff_winner'])}")
    print(f"=== ERP priced, TOS blank: {len(cat['erp_only'])}")
    print(f"=== TOS priced, ERP blank: {len(cat['tos_only'])}")
    print(f"=== both blank: {len(cat['both_blank'])}")

    def dump(tag, pairs, n=60):
        print(f"\n--- {tag} ---")
        for t, e, ecode in pairs[:n]:
            print(f"  L{e['line_no']} | {str(t['desc'])[:58]}")
            print(f"      TOS: {t['tier']} @{t['price']} <- {str(t['code'])[:30]} {str(t['cdesc'])[:40]}")
            print(f"      ERP: {e['tier']} @{e.get('suggested_rate')} <- {ecode[:30]} conf={e.get('confidence')}")

    dump("ERP priced / TOS blank", cat["erp_only"])
    dump("TOS priced / ERP blank", cat["tos_only"])
    dump("different winner", cat["diff_winner"])
    print("\n--- both blank ---")
    for t, e, _ in cat["both_blank"]:
        print(f"  L{e['line_no']} {str(t['desc'])[:70]}")

    with open(r"C:\lab\tenderops\platform\_diff.json", "w", encoding="utf-8") as f:
        json.dump({k: [
            {"line": e["line_no"], "tos_desc": t["desc"], "tos_tier": t["tier"],
             "tos_price": str(t["price"]), "tos_code": str(t["code"]),
             "erp_tier": e["tier"], "erp_rate": str(e.get("suggested_rate")),
             "erp_code": ec} for t, e, ec in v] for k, v in cat.items()},
            f, ensure_ascii=False, indent=1)


asyncio.run(main())
