# -*- coding: utf-8 -*-
"""Headless full-pipe driver: same path the web server takes, without the server.

    .venv/Scripts/python.exe _headless_run.py https://app.eop.bg/today/601701 [--force]

  extra legs:
    --local <path>   attach a local ЦАИС-export file as a document of the tender
                     (repeatable; name carries "[локален експорт]" provenance)
    --submit         after a successful pack, build the submission quintet + zip
                     (app/submission.build_submission — same as /api/tender/<id>/submission.zip)

Parity with app/server.py:
  /api/tenders/add            -> eop.fetch_tender + registry upsert + workflow.register_tender
  /api/tender/<id>/process    -> pipeline.process_tender(rec, DEMO, PROCESSED, llm_settings)

Writes go to the production surfaces (data/demo, root tenderops.sqlite3).
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent
DEMO = ROOT / "data" / "demo"
PROCESSED = DEMO / "processed"
REGISTRY = DEMO / "registry.json"
SETTINGS = DEMO / "settings.json"


def mod(name):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "app" / f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def main():
    rest = sys.argv[1:]
    force = "--force" in rest
    submit = "--submit" in rest
    local_paths = []
    args = []
    it = iter(range(len(rest)))
    skip = False
    for i, a in enumerate(rest):
        if skip:
            skip = False
            continue
        if a in ("--force", "--submit"):
            continue
        if a == "--local":
            if i + 1 >= len(rest):
                print("usage: --local <path>")
                return 2
            local_paths.append(rest[i + 1])
            skip = True
            continue
        args.append(a)
    if not args:
        print("usage: _headless_run.py <tender-ref|url> [--force] [--local <path> ...] [--submit]")
        return 2
    ref = args[0]

    eop = mod("eop")
    costdb = mod("costdb")
    pipe = mod("pipeline")

    tid = eop.parse_tender_ref(ref)
    if not tid:
        print(f"[headless] unrecognised tender ref: {ref!r}")
        return 2
    print(f"[headless] tender ref {ref!r} -> id {tid}")

    # --- 1. acquisition (live public ЦАИС ЕОП API) ---
    t0 = time.time()
    rec = eop.fetch_tender(tid)
    print(f"[headless] ACQUIRED {rec['id']} «{rec['name'][:80]}» buyer={rec['buyer'][:60]}")
    print(f"[headless]   number={rec['number']} deadline={rec['deadline']} est={rec['estValue']} {rec['currency']}")
    print(f"[headless]   docs={len(rec['documents'])} exports={len(rec['exports'])} announcements={len(rec['announcements'])} (+{time.time()-t0:.1f}s)")

    # --- 1.5 local-export attachments (ЦАИС Експорт docs not exposed at register level) ---
    if local_paths:
        import hashlib as _hl
        fdir = PROCESSED / "files" / str(tid)
        fdir.mkdir(parents=True, exist_ok=True)
        for lp in local_paths:
            src = Path(lp)
            if not src.exists():
                print(f"[headless] --local missing file: {lp}")
                return 2
            payload = src.read_bytes()
            did = "exp" + _hl.sha1(payload).hexdigest()[:12]
            safe_name = src.name[:120]
            dst = fdir / f"{did}_{safe_name}"
            if not dst.exists():
                dst.write_bytes(payload)
            rec.setdefault("documents", []).append({
                "name": f"{src.stem} [локален експорт]{src.suffix.lower()}",
                "docId": did, "size": len(payload), "ext": src.suffix.lower(),
                "modified": None, "localPath": str(dst)})
            print(f"[headless]   +local doc: {src.name} -> {dst.name}")

    # --- 2. registry upsert + workflow registration (server parity) ---
    reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig")) if REGISTRY.exists() else {"tenders": []}
    reg["tenders"] = [t for t in reg.get("tenders", []) if t.get("id") != tid]
    reg["tenders"].append(rec)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    dbp = costdb.db_path_for(DEMO)
    try:
        wf = mod("workflow")
        sel = wf.register_tender(str(dbp), rec)
        print(f"[headless]   registry upserted; workflow registered (state={sel.get('state') if isinstance(sel, dict) else sel})")
    except Exception as ex:
        print(f"[headless]   workflow register skipped: {ex.__class__.__name__}: {ex}")
    print(f"[headless]   costdb = {dbp}")

    # --- 3. LLM settings probe (advisory leg; graceful when offline) ---
    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    llm_cfg = settings.get("llm") or {}
    if llm_cfg.get("base_url"):
        try:
            llm = mod("llm")
            ok, note, resolved = llm.test_conn(llm_cfg.get("base_url"), llm_cfg.get("model"),
                                               api_key=llm_cfg.get("api_key") or None)
            print(f"[headless]   LLM {llm_cfg.get('base_url')} model={llm_cfg.get('model')}: {'ONLINE ' + str(resolved) if ok else 'OFFLINE (' + str(note)[:80] + ')'}")
        except Exception as ex:
            print(f"[headless]   LLM probe failed: {ex}")

    # --- 4. full pipeline ---
    t1 = time.time()
    res = pipe.process_tender(rec, DEMO, PROCESSED, llm_settings=llm_cfg or None)
    dt = time.time() - t1
    if not res.get("ok"):
        print(f"[headless] PIPE FAILED: {res.get('error')} (cancelled={res.get('cancelled')})")
        return 1
    pk = res["pack"]
    priced = sum(1 for l in pk["boq"] if l["rule"] == "CSV")
    print(f"[headless] PIPE OK in {dt:.1f}s -> {res.get('path')}")
    print(f"[headless]   pipe stages: {json.dumps(pk['pipe'], ensure_ascii=False)}")
    print(f"[headless]   boq={len(pk['boq'])} priced={priced} est={len(pk['boq'])-priced}")
    print(f"[headless]   totalExclVat={pk['pricing']['totalExclVat']} {pk['pricing']['currency']} "
          f"cap={pk['pricing'].get('capExclVat')} vat={pk['pricing']['vat']} incl={pk['pricing']['totalInclVat']}")
    print(f"[headless]   env={pk['pricing'].get('costEnvVersion')} ({pk['pricing'].get('costVersion')})")
    print(f"[headless]   llm.ok={pk['llm'].get('ok')} ({(pk['llm'].get('error') or 'ok')[:100]})")
    from collections import Counter
    methods = Counter(l["match"]["method"] for l in pk["boq"] if l.get("match") and l["rule"] == "CSV")
    print(f"[headless]   match methods: {dict(methods)}")
    rules = {r["id"]: r["status"] for r in pk.get("rules", [])}
    print(f"[headless]   rules: {rules}")
    print(f"[headless]   similar tenders: {[s.get('tender_id') for s in pk.get('similar', [])][:5]}")
    try:
        import sqlite3 as _sq
        con = _sq.connect(str(dbp), timeout=10)
        rid = con.execute("SELECT id FROM runs WHERE tender_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        con.close()
        if rid:
            print(f"[headless]   run_id={rid[0]}  (forensics: tenderops run show {rid[0]} / run events {rid[0]})")
    except Exception:
        pass

    # --- 5. submission quintet + zip (app/submission.build_submission) ---
    if submit:
        sub = mod("submission")
        man = sub.build_submission(rec, pk, PROCESSED, PROCESSED)
        zp = PROCESSED / f"submission_{tid}.zip"
        print(f"[headless] SUBMISSION -> {zp} ({(zp.stat().st_size // 1024) if zp.exists() else '?'} KB)")
        for f in man.get("files", []):
            extra = f" [{f.get('mode')}]" if f.get("mode") else ""
            print(f"[headless]   - {f['file']}: {f['what']}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
