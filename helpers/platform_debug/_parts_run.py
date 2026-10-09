# Segmented manual run: parse the Hisarya KCC, split positions by the workbook's
# own "Част:" boundaries, price every row against the local corpus (policy A:
# store/auto commit, active>=55 commit, pending -> provisional, else missing),
# and emit one populated XML per part to C:\lab\tenderops.
import json, re, importlib.util
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
XLS = Path(r"C:\lab\tenderops\20261002_160236_КС -  Хисаря-всички части - за Възложителя.xls")
DB = ROOT / "tenderops.sqlite3"
STORE = ROOT / "data/demo/estimation_prices.json"
OUTDIR = Path(r"C:\lab\tenderops")
FLOOR = 55.0

def _mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

cdb, pp = _mod("costdb"), _mod("pipeline")
store = json.loads(STORE.read_text(encoding="utf-8-sig")) if STORE.exists() else {}

PART_RE = re.compile(r"^\s*част\s*[:.]?\s*", re.I)
PART_SLUG = [  # longest-key-first: "технология - басейни" must beat bare "технология"
    ("технология - басейни", "Baseyni"), ("строителни конструкции", "Konstrukcii"),
    ("паркоустройство", "Parko"), ("фотоволтаична", "PV"), ("електро", "EL"),
    ("технология", "Bistro"), ("в и к", "ViK"), ("овк", "OVK"), ("ас", "AS"),
]

def part_of(text):
    m = PART_RE.match(str(text or ""))
    if not m:
        return None
    rest = str(text)[m.end():].strip()
    low = rest.lower()
    for k, slug in PART_SLUG:
        if low.startswith(k):
            return slug, "Част: " + rest
    return None  # "част" inside a desc (e.g. "частично") — not a boundary

def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()

# ---- 1. parse --------------------------------------------------------------
rows = pp.parse_kss_priced(XLS)
print(f"parsed {len(rows)} rows")

# ---- 2. align parsed rows to sheet positions, tracking Част boundaries -------
raw = [(sec, vals) for sec, vals in pp._scoped_sheet_rows(XLS)]
part_seq = []           # current part per raw row index
cur = ("_head", "—")
cursor, miss = 0, []
aligned = []            # (parsed_row, part_slug, part_name)
for pr in rows:
    pdesc, pqty = norm(pr.get("desc")), float(pr.get("qty") or 0)
    hit = -1
    for j in range(cursor, len(raw)):
        vals = raw[j][1]
        cells = [norm(v) for v in vals]
        if pdesc and pdesc in cells:
            # qty check: any numeric cell equal to parsed qty
            ok = False
            for v in vals:
                try:
                    if abs(float(v) - pqty) < 1e-6:
                        ok = True
                except (TypeError, ValueError):
                    pass
            if ok:
                hit = j
                break
    if hit < 0:
        miss.append(pr)
        continue
    # advance part tracker through rows [cursor..hit]
    for j in range(cursor, hit + 1):
        for v in raw[j][1]:
            p = part_of(v)
            if p:
                cur = p
                break
    aligned.append((pr, cur[0], cur[1]))
    cursor = hit + 1
print(f"aligned {len(aligned)} rows to parts | unaligned {len(miss)}")

# ---- 3. classify each row (policy A) -----------------------------------------
def classify(r):
    mk = norm(r.get("desc")) + " ∥ " + (r.get("unit") or "бр").strip()
    if mk in store:
        e = store[mk]
        return ("manual", float(e["price"]), e.get("note") or "operator",
                e.get("actor") or "operator", None)
    best, score, ev = pp.match_cost_v2(r, str(DB))
    cands = (ev or {}).get("top_candidates") or []
    if best is not None:
        return ("auto", float(best.get("unitEur") or 0),
                f"{best.get('ref')} · score {score:.1f}", best.get("status"), None)
    act = next((c for c in cands if c.get("status") == "active"
                and float(c.get("score") or 0) >= FLOOR), None)
    if act is not None:
        return ("candidate_active", float(act["unitEur"]),
                f"{act.get('ref')} · score {act.get('score')}", "active", act.get("id"))
    pend = next((c for c in cands if c.get("status") == "pending_review"
                 and c.get("unitEur")), None)
    if pend is not None:
        return ("pending", float(pend["unitEur"]),
                f"{pend.get('ref')} · score {pend.get('score')}", "pending_review",
                pend.get("id"))
    return ("missing", None, None, None, None)

parts = {}
for pr, slug, pname in aligned:
    kind, price, ref, status, cid = classify(pr)
    parts.setdefault((slug, pname), []).append((pr, kind, price, ref, status, cid))
for pr in miss:
    parts.setdefault(("_unaligned", "Неразпределени"), []).append(
        (pr, "missing", None, "row alignment failed", None, None))

# ---- 4. XML per part ----------------------------------------------------------
gen = pp.time.strftime("%Y-%m-%d %H:%M:%S") if hasattr(pp, "time") else __import__("time").strftime("%Y-%m-%d %H:%M:%S")
written = []
grand = {"committed": 0.0, "pending": 0.0}
for (slug, pname), items in parts.items():
    comm = sum(q * p for (r, k, p, *_x) in items for q in [float(r.get("qty") or 0)]
               if p is not None and k in ("manual", "auto", "candidate_active"))
    pend = sum(float(r.get("qty") or 0) * p for (r, k, p, *_x) in items
               if p is not None and k == "pending")
    grand["committed"] += comm; grand["pending"] += pend
    n_p = sum(1 for _, k, p, *_x in items if p is not None and k != "pending")
    n_pd = sum(1 for _, k, *_x in items if k == "pending")
    n_m = sum(1 for _, k, *_x in items if k == "missing")
    x = ['<?xml version="1.0" encoding="utf-8"?>',
         f'<estimatePart project="Общински плувен комплекс Хисаря" part="{escape(pname)}"',
         f'  source="{escape(XLS.name)}" generated="{gen}"',
         f'  positions="{len(items)}" priced="{n_p}" pending="{n_pd}" missing="{n_m}"',
         f'  committedEur="{comm:.2f}" pendingPoolEur="{pend:.2f}">']
    for r, kind, price, ref, status, cid in items:
        attrs = f'no="{r.get("row","")}" unit="{escape(str(r.get("unit") or ""))}" qty="{r.get("qty")}"'
        x.append(f'  <position {attrs}>')
        x.append(f'    <desc>{escape(str(r.get("desc") or ""))}</desc>')
        if price is None:
            x.append('    <price kind="missing"/>')
        else:
            qty = float(r.get("qty") or 0)
            a = f'kind="{kind}" eur="{price:.4f}" sumEur="{qty * price:.2f}"'
            if status:
                a += f' status="{status}"'
            if cid:
                a += f' candidateId="{escape(str(cid))}"'
            x.append(f'    <price {a}>')
            if ref:
                x.append(f'      <provenance>{escape(str(ref))}</provenance>')
            x.append('    </price>')
        x.append('  </position>')
    x.append('</estimatePart>')
    fn = OUTDIR / f"Hisarya_KC_{len(written):02d}_{slug}.xml"
    fn.write_text("\n".join(x), encoding="utf-8")
    written.append((fn.name, len(items), n_p, n_pd, n_m, comm, pend))
    print(f"  {fn.name:28s} {len(items):4d} rows | priced {n_p:4d} | pending {n_pd:4d} | missing {n_m:3d} | {comm:>13,.2f} + {pend:>13,.2f} pending")

print(f"\nGRAND committed {grand['committed']:,.2f} EUR | pending pool {grand['pending']:,.2f} EUR")
