# -*- coding: utf-8 -*-
"""Seed three Spatial-v2 demo tenders through the REAL pipeline:
registry records with local documents (same shape /attach writes) -> POST /process
-> verify /spatial + /spatial/geometry.
Run: PYTHONIOENCODING=utf-8 venv/Scripts/python.exe _spatial_demo_seed.py
"""
import hashlib, json, shutil, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:9090"
FX = ROOT / "tests" / "spatial_fixtures"
FILES_DIR = ROOT / "data" / "demo" / "processed" / "files"

DEMOS = [
    (990001, "ДЕМО: Еднофамилна къща L 14.4×10.8м, 2 етажа",
     ["house_l.dxf", "house_spec.pdf"],
     [("Земни работи", [("Изкоп за фундаменти", "куб.м.", 42.0)]),
      ("Конструктивни", [("Бетон C25/30 фундаменти", "куб.м.", 18.5),
                         ("Стоманобетонни плочи", "куб.м.", 12.0),
                         ("Зидачни стени", "куб.м.", 55.0)])]),
    (990002, "ДЕМО: Училище U 54.0×36.0м, 3 етажа, вътрешен двор",
     ["school_u.dxf", "school_spec.pdf", "school_spec.docx"],
     [("Земни работи", [("Изкоп за фундаменти", "куб.м.", 260.0)]),
      ("Конструктивни", [("Бетон C25/30 фундаменти", "куб.м.", 145.0),
                         ("Стоманобетонни плочи", "куб.м.", 220.0),
                         ("Зидачни стени", "куб.м.", 380.0)])]),
    (990003, "ДЕМО: Хангар 72.0×30.0м, 1 етаж, 14.2м",
     ["hangar_72.dxf", "hangar_spec.pdf"],
     [("Земни работи", [("Изкоп за фундаменти", "куб.м.", 300.0)]),
      ("Конструкции", [("Бетон C25/30 фундаменти", "куб.м.", 180.0),
                       ("Стоманена конструкция портални рамки", "т", 48.0),
                       ("Стенни панели", "кв.м.", 1500.0)])]),
]


def build_kss(path, sections):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(("Количествена сметка", None, None, None))
    ws.append(("№", "Описание на строително-монтажните работи", "Мярка",
               "Одобрено количество"))
    ws.append((1, 2, 3, 4))
    no = 1
    for sec, rows in sections:
        ws.append((f'Част "{sec}"', None, None, None))
        for desc, unit, qty in rows:
            ws.append((no, desc, unit, qty))
            no += 1
    wb.save(path)


def post(path, body=b""):
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=600) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    reg_p = ROOT / "data" / "demo" / "registry.json"
    reg = json.loads(reg_p.read_text(encoding="utf-8"))
    tenders = reg["tenders"]
    for tid, name, files, sections in DEMOS:
        tenders[:] = [t for t in tenders if t.get("id") != tid]
        # stage files exactly like /api/tender/<id>/attach would (local docs)
        fdir = FILES_DIR / str(tid)
        fdir.mkdir(parents=True, exist_ok=True)
        docs = []
        payloads = [("kss_demo.xlsx", build_kss(fdir / "kss_demo.xlsx", sections))]
        payloads += [(f, None) for f in files]
        for fn, _ in payloads:
            src = fdir / fn if fn == "kss_demo.xlsx" else FX / fn
            data = src.read_bytes()
            did = "demo" + hashlib.sha1(data).hexdigest()[:10]
            dst = fdir / f"{did}_{fn}"
            if fn != "kss_demo.xlsx":
                shutil.copyfile(src, dst)
            else:
                dst = fdir / f"{did}_kss_demo.xlsx"
                shutil.move(str(src), dst)
            docs.append({"name": fn, "docId": did, "ext": Path(fn).suffix.lower(),
                         "size": len(data), "modified": None, "localPath": str(dst)})
        tenders.append({"id": tid, "name": name, "buyer": "TenderOps ДЕМО",
                        "number": f"DEMO-{tid}", "estValue": None, "currency": "EUR",
                        "deadline": "", "procedureType": 12, "typeOfContract": 3,
                        "url": "", "acquiredAt": "2026-09-10 10:00:00",
                        "state": "spatial v2 demo", "documents": docs})
        print(f"{tid}: staged {len(docs)} documents")
    reg_p.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    # drop stale processed packs so /process recomputes fresh
    for tid, *_ in DEMOS:
        for p in (ROOT / "data" / "demo" / "processed").glob(f"{tid}.*json"):
            p.unlink()

    for tid, name, files, sections in DEMOS:
        res = post(f"/api/tender/{tid}/process")
        boq_n = len(res.get("boq", [])) if isinstance(res, dict) else "?"
        print(f"{tid}: /process -> boq rows={boq_n}")
        g = get(f"/api/tender/{tid}/spatial/geometry")
        m = get(f"/api/tender/{tid}/spatial")
        s2 = m.get("scene_v2") or {}
        dims = (g.get("dimensions") or {})
        dmsg = " ".join(f"{k}={ (dims.get(k) or {}).get('value') }"
                        for k in ("length_m", "width_m", "height_m", "storeys"))
        print(f"{tid}: geometry={g.get('present')} readiness={m['project']['readiness']} "
              f"objects={len(s2.get('objects', []))} suppress_art={s2.get('suppress_legacy_art')} | {dmsg}")
    print("DONE")


if __name__ == "__main__":
    main()
