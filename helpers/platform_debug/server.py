# -*- coding: utf-8 -*-
r"""
TenderOps — demo server (stdlib only).
Run:  python app\server.py   →  http://0.0.0.0:9000
Reads only from data/demo/*.json (built by data/build_demo.py + mine_2026_consolidate.py).
"""
import json, os, re, shutil, sys, time, urllib.error, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_BASE = os.environ.get("TENDEROPS_BASE") or str(ROOT)
DEMO = Path(_BASE) / "data" / "demo"
WEB = Path(_BASE) / "app" / "web"
# user-writable bases: when frozen, write NEXT to the exe (durable); else inside the project
_exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
_res_base = _exe_dir if _exe_dir else Path(os.environ.get("TENDEROPS_WRITEBASE", _BASE))
RESOLUTIONS = _res_base / "data" / "demo" / "resolutions.json"
HOST, PORT = os.environ.get("TENDEROPS_HOST", "0.0.0.0"), int(os.environ.get("TENDEROPS_PORT", "9000"))

_cache = {}
REGISTRY = _res_base / "data" / "demo" / "registry.json"
SETTINGS = _res_base / "data" / "demo" / "settings.json"
PROCESSED = _res_base / "data" / "demo" / "processed"
# canonical store: SQLite next to the exe (or project root). JSON seeds = import/export boundary.
os.environ.setdefault("TENDEROPS_DB_PATH", str(_res_base / "tenderops.sqlite3"))

_TS_PREFIX = re.compile(r"^\d{8}_\d{6}_")

def _is_estimation_artifact(name):
    """True for files the estimation flow itself produced (KSS outputs, pack JSONs,
    manifest) — including user re-uploads of them, which land in the inbox with a
    scan timestamp prefix. Such files carry no price authority beyond the run that
    produced them; feeding them back only duplicates scope, so runs skip them LOUDLY."""
    base = _TS_PREFIX.sub("", name)
    return base.startswith(("KSS_ocenena_", "estimation_", "manifest.json"))

def _mod(name):
    """Sibling module loader that works as script, package, or frozen exe."""
    import importlib.util as _ilu
    p = Path(__file__).resolve().parent / f"{name}.py"
    if not p.is_file():
        p = Path(__file__).resolve().parent / "app" / f"{name}.py"
    spec = _ilu.spec_from_file_location(name, str(p))
    m = _ilu.module_from_spec(spec); spec.loader.exec_module(m)
    return m

_RUNNING = {}
_RUNNING_LOCK = __import__("threading").Lock()
_SPATIAL = {}  # tid -> threading.Event; scoped op — spatial cancel touches nothing else
_EST_RUNNING = False      # single-flight: една estimation оценка по едно време (B3/B4)
_EST_CANCEL = None        # threading.Event докато run тече; /api/estimation/cancel я вдига

workflow = _mod("workflow")  # app/workflow.py — investor identity/tenant layer (route bodies use it)
try:
    telemetry = _mod("telemetry")
except Exception:
    telemetry = None

def _find_tender(tid):
    local = workflow.get_by_engine_id(os.environ['TENDEROPS_DB_PATH'], tid)
    if local:
        return local
    if tid < 0:
        return None
    # 1) регистър (придобитите имат пълен списък с документи)
    for t in registry().get("tenders", []):
        if t.get("id") == tid and t.get("documents"):
            return t
    # 2) няма пълен запис → издърпай публичното ниво на живо и го запиши в регистъра
    try:
        rec = _mod("eop").fetch_tender(tid)
    except Exception:
        rec = None
    if rec:
        reg = registry()
        reg["tenders"] = [t for t in reg.get("tenders", []) if t.get("id") != tid]
        reg["tenders"].append(rec)
        registry_save(reg)
        return rec
    # 3) fallback: вградените demo-записи (без документи → честен празен пакет)
    for t in (load("tenders.json") or []):
        if t.get("id") == tid:
            return t
    return None

def _ensure_user_files():
    """On first run of the frozen exe: materialize the bundled demo tree next to the exe
    so user additions (registry/resolutions) persist across runs."""
    RESOLUTIONS.parent.mkdir(parents=True, exist_ok=True)
    if not RESOLUTIONS.exists():
        RESOLUTIONS.write_text("{}", encoding="utf-8")
    if not REGISTRY.exists():
        REGISTRY.write_text(json.dumps({"tenders": []}, ensure_ascii=False), encoding="utf-8")
    if not SETTINGS.exists():
        SETTINGS.write_text(json.dumps({"llm": {"base_url": "http://127.0.0.1:10000", "api_key": "", "model": ""}}, ensure_ascii=False, indent=1), encoding="utf-8")

def settings():
    if SETTINGS.exists():
        return json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    return {"llm": {"base_url": "http://127.0.0.1:10000", "api_key": "", "model": ""}}

def settings_save(s):
    SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")

def _mask(key):
    if not key:
        return ""
    k = str(key)
    return k[:4] + "…" + k[-4:] if len(k) > 10 else "••••"

def registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    return {"tenders": []}

def registry_save(reg):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")

def load(name):
    if name not in _cache:
        p = DEMO / name
        _cache[name] = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else None
    return _cache[name]


def _load_pack_for(tid):
    """Processed pack for a tender. An EMPTY pack (0 BOQ rows) is not a pack —
    owner rule 2026-09-06: such files must never be produced; legacy ones are
    treated as 'not processed' so the UI hands the tender back to preparation."""
    proc = PROCESSED / f"{tid}.json"
    if proc.exists():
        try:
            pk = json.loads(proc.read_text(encoding="utf-8-sig"))
        except Exception:
            return None
        if not (pk.get("boq") or []):
            print(f"[server] ignoring empty pack {proc.name} (0 BOQ rows) — tender counts as not processed")
            return None
        return pk
    return None


def _geometry_cache_dir():
    """data/geometry_cache — where cached_extract persists (pipeline writes, we serve).
    Resolved at call time so test/embedded bases that repoint DEMO stay consistent."""
    return Path(DEMO).parent / "geometry_cache"


def _candidate_price(cdb, dbp, item_id):
    """Net EUR unit price of a cost_items row (resolve-all source). None when unknown."""
    if not item_id:
        return None
    try:
        con = cdb.connect(dbp)
        try:
            r = con.execute("SELECT amount_eur, vat_included FROM cost_items WHERE id=?",
                            (item_id,)).fetchone()
            if not r:
                return None
            amount = float(r["amount_eur"])
            # същата VAT нормализация като pipeline (÷1.2 when vat_included) — netto EUR
            net = amount / 1.2 if r["vat_included"] else amount
            return round(net, 4) if net > 0 else None
        finally:
            con.close()
    except Exception:
        return None


def _estimation_prices_path():
    """Operator-entered estimation prices live at data/demo/estimation_prices.json —
    OUTSIDE the wiped inbox, so a cleared session no longer deletes human decisions
    (A1, canon HUMAN DOOR). One-time merge from the legacy inbox manual_prices.json."""
    p = DEMO / "estimation_prices.json"
    legacy = DEMO / "estimation_inbox" / "manual_prices.json"
    if legacy.exists():
        try:
            store = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else {}
            old = json.loads(legacy.read_text(encoding="utf-8-sig")) or {}
            store.update(old)  # по-новият (session) файл печели при ключов конфликт
            p.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
            legacy.unlink()
            print(f"[server] migrated {len(old)} legacy manual price(s) -> estimation_prices.json")
        except Exception as ex:
            print(f"[server] manual price migration failed loudly: {ex}")
    return p

_RES_IMPORTED = False

def _generation_guard(self, tid, pack):
    """Canon: no generated instrument without persisted human review.
    Gate = open blockers unresolved OR the explicit approval missing.
    Returns None when clean, else a 409-ready payload."""
    try:
        cdb = _mod("costdb")
        dbp = os.environ.get("TENDEROPS_DB_PATH")
        res = cdb.get_resolutions(dbp)
    except Exception:
        return {"code": "REVIEW_REQUIRED", "error": "не мога да прочета портите (DB); генерация спряна"}
    open_gates = [l.get("key") for l in pack.get("boq", [])
                  if (l.get("rule") == "EST" or l.get("flag")) and l.get("key") not in res]
    approved = bool(res.get("__approved__"))
    if open_gates or not approved:
        how = []
        if open_gates:
            how.append(f"разреши {len(open_gates)} позиции в Риск (или resolve-all с бележка)")
        if not approved:
            how.append("изрично потвърждение: бутон „Одобри и генерирай“ след преглед")
        return {"code": "REVIEW_REQUIRED", "ok": False,
                "openGates": open_gates, "approved": approved,
                "error": f"генерацията е блокирана до човешки преглед ({'; '.join(how)})"}
    return None


def resolutions(tender_id=None):
    """Canonical human resolutions now live in tenderops.sqlite3 (human_resolutions).
    Legacy resolutions.json is imported once, then remains an EXPORT only.
    tender_id given → that tender's scope only (canon: чужди решения не прескачат)."""
    global _RES_IMPORTED
    cdb = _mod("costdb")
    env = os.environ.get("TENDEROPS_DB_PATH")
    if not _RES_IMPORTED:
        try:
            n = cdb.import_resolutions_json(env, RESOLUTIONS)
            if n:
                print(f"resolutions: imported {n} legacy rows -> SQLite")
        except Exception as ex:
            print(f"resolutions legacy import failed loudly: {ex}")
        _RES_IMPORTED = True
    return cdb.get_resolutions(env, tender_id)

def save_resolution(key, note, by, tender_id=None):
    cdb = _mod("costdb")
    env = os.environ.get("TENDEROPS_DB_PATH")
    cdb.upsert_resolution(env, tender_id, key, note, by)
    out = cdb.get_resolutions(env)
    try:  # export for compat; the DB is authoritative — JSON mirrors it only
        RESOLUTIONS.parent.mkdir(parents=True, exist_ok=True)
        RESOLUTIONS.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as ex:
        print(f"resolution export fail: {ex}")
    return out[key]

class H(BaseHTTPRequestHandler):
    def _get_corr_id(self):
        return self.headers.get("X-Correlation-ID") or (telemetry._generate_correlation_id() if telemetry else "corr-none")

    def _get_session_id(self):
        return self.headers.get("X-Session-ID") or "sess-default"

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        corr = getattr(self, "_corr_id", None) or self._get_corr_id()
        self.send_header("X-Correlation-ID", corr)
        self.end_headers()
        self.wfile.write(body)
        if telemetry:
            try:
                t0 = getattr(self, "_req_t0", None)
                dur = (time.time() - t0) * 1000.0 if t0 else None
                telemetry.log_event(
                    telemetry.EventType.API_RESPONSE,
                    message=f"{self.command} {self.path} -> {code}",
                    severity=telemetry.Severity.INFO if code < 400 else (telemetry.Severity.WARNING if code < 500 else telemetry.Severity.ERROR),
                    route=self.path,
                    operation=self.command,
                    status=str(code),
                    duration_ms=round(dur, 2) if dur else None,
                    correlation_id=corr,
                    session_id=self._get_session_id(),
                    component="server",
                    payload={"status_code": code, "path": self.path}
                )
            except Exception:
                pass

    def _static(self, path):
        p = WEB / path.lstrip("/")
        if not p.exists() or not p.is_file():
            self.send_error(404); return
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
                 ".glb": "model/gltf-binary", ".wasm": "application/wasm"}.get(p.suffix, "application/octet-stream")
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._req_t0 = time.time()
        self._corr_id = self._get_corr_id()
        if telemetry:
            try:
                telemetry.log_event(
                    telemetry.EventType.API_REQUEST,
                    message=f"GET {self.path}",
                    severity=telemetry.Severity.INFO,
                    route=self.path,
                    operation="GET",
                    correlation_id=self._corr_id,
                    session_id=self._get_session_id(),
                    component="server"
                )
            except Exception:
                pass
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/tender/jobstatus":
            # Job panel feed: latest run + journal for one tender (the drawer polls this).
            tq = (q.get("tender_id") or [""])[0]
            cdb = _mod("costdb")
            con = cdb.connect(os.environ.get("TENDEROPS_DB_PATH"))
            try:
                row = con.execute("SELECT id,state,started_at,finished_at,note FROM runs WHERE tender_id=? ORDER BY id DESC LIMIT 1",
                                  (int(tq) if tq.isdigit() else -1,)).fetchone()
                if not row:
                    return self._json({"ok": True, "run": None})
                evs = con.execute("SELECT at,state,detail FROM run_events WHERE run_id=? ORDER BY id ASC", (row["id"],)).fetchall()
                return self._json({"ok": True, "run": {"id": row["id"], "state": row["state"],
                                   "started_at": row["started_at"], "finished_at": row["finished_at"],
                                   "note": row["note"]},
                                   "events": [dict(e) for e in evs]})
            finally:
                con.close()
        if u.path == "/api/telemetry/events":
            run_id = int(q["run_id"][0]) if "run_id" in q and q["run_id"][0].isdigit() else None
            tender_id = int(q["tender_id"][0]) if "tender_id" in q and q["tender_id"][0].isdigit() else None
            sev = q.get("severity", [None])[0]
            etype = q.get("event_type", [None])[0]
            since = q.get("since", [None])[0]
            limit = int(q["limit"][0]) if "limit" in q and q["limit"][0].isdigit() else 100
            evs = telemetry.query_events(os.environ.get("TENDEROPS_DB_PATH"), run_id=run_id, tender_id=tender_id, severity=sev, event_type=etype, since=since, limit=limit) if telemetry else []
            return self._json({"ok": True, "count": len(evs), "events": [e.to_dict() for e in evs]})
        if u.path == "/api/telemetry/diagnose":
            diag = telemetry.diagnose_system(os.environ.get("TENDEROPS_DB_PATH")) if telemetry else {"status": "unknown"}
            return self._json(diag)
        if u.path in ("/", "/index.html"):
            return self._static("/index.html")
        if u.path == '/api/projects':
            return self._json(workflow.list_projects(os.environ['TENDEROPS_DB_PATH']))
        if u.path in ("/app.css", "/app.js", "/logo.svg", "/renderer.js", "/i18n.js", "/i18n-spatial.js", "/config.example.js") or (not u.path.startswith("/api/") and (WEB / u.path.lstrip("/")).is_file()):
            return self._static(u.path)
        if u.path == "/api/tenders":
            by_id = {t.get("id"): dict(t) for t in (load("tenders.json") or [])}
            for t in registry().get("tenders", []):
                overlay = {"id": t["id"], "name": t["name"], "buyer": t["buyer"], "number": t["number"],
                           "cpv": None, "deadline": t.get("deadline"), "openDate": None,
                           "estValue": t.get("estValue"), "currency": t.get("currency", "EUR"),
                           "offerExclVat": None, "offerInclVat": None,
                           "procedure": f'acquired {t.get("acquiredAt","")}',
                           "criterion": None, "url": t.get("url") or None, "state": t.get("state", ""),
                           "executionDays": None, "proposedDays": None, "validityDays": None,
                           "guaranteePercent": None, "_registry": True,
                           "_docs": t.get("documents", []), "_anns": t.get("announcements", []),
                           "_desc": t.get("descriptionHtml", "")}
                base = by_id.get(t["id"], {})
                merged = dict(base)
                for k, v in overlay.items():
                    if v is None and base.get(k) is not None:
                        continue  # пази builtin стойността
                    merged[k] = v
                by_id[t["id"]] = merged
            for tid, t in by_id.items():
                p = PROCESSED / f"{tid}.json"
                if p.exists():
                    try:
                        pk = json.loads(p.read_text(encoding="utf-8-sig"))
                        if not (pk.get("boq") or []):
                            raise ValueError("empty pack — not a completed run")
                        t["offerExclVat"] = pk.get("pricing", {}).get("totalExclVat")
                        t["offerInclVat"] = pk.get("pricing", {}).get("totalInclVat")
                        t["state"] = "пакет готов"
                    except Exception:
                        t["offerExclVat"] = None
                        t["offerInclVat"] = None
                else:
                    t["offerExclVat"] = None
                    t["offerInclVat"] = None
            now = time.strftime("%Y-%m-%d %H:%M")
            def _key(t):
                d = t.get("deadline") or ""
                expired = bool(d) and d[:16].replace("T", " ") < now
                return (2 if expired else 0, d or "9999")
            return self._json(sorted(by_id.values(), key=_key))
        if u.path == "/api/trace":
            return self._json(load("golden_trace.json"))
        if u.path == "/api/settings/llm":
            s = settings().get("llm", {})
            out = dict(s)
            out["api_key_masked"] = _mask(s.get("api_key", ""))
            out.pop("api_key", None)
            return self._json(out)
        if u.path == "/api/costdb/search":
            query = (q.get("q") or [""])[0].strip()
            unit = (q.get("unit") or [None])[0]
            cdb = _mod("costdb")
            cdb.sync_json_sources(DEMO, os.environ.get("TENDEROPS_DB_PATH"))
            rows = cdb.search_candidates(os.environ.get("TENDEROPS_DB_PATH"), query, unit, limit=30)
            out = [{"id": r.get("id"), "desc": r.get("desc"), "unit": r.get("unit"),
                    "amount": r.get("amount_eur"), "currency": r.get("currency"),
                    "vatIncl": bool(r.get("vat_included")), "asOf": r.get("as_of"),
                    "source": r.get("source_key"), "ref": r.get("origin_ref"),
                    "klass": r.get("origin_kind"), "status": r.get("status")} for r in rows]
            return self._json(out)
        if u.path == "/api/costdb":
            try:
                cdb = _mod("costdb")
                cdb.sync_json_sources(DEMO, os.environ.get("TENDEROPS_DB_PATH"))
                rows = []
                for r in cdb.get_active_rows(os.environ.get("TENDEROPS_DB_PATH")):
                    try:
                        extra = json.loads(r.get("extra_json") or "{}")
                    except Exception:
                        extra = {}
                    rows.append({
                        "id": r.get("id"), "name": r.get("name"), "desc": r.get("desc"),
                        "category": r.get("category"), "section": r.get("section"), "unit": r.get("unit"),
                        "money": {"amount": r.get("original_amount"), "currency": r.get("currency"),
                                  "vatIncluded": bool(r.get("vat_included")), "asOf": r.get("as_of")},
                        "components": extra.get("components") or {},
                        "source": r.get("source_key"), "origin": {"kind": r.get("origin_kind"),
                                "ref": r.get("origin_ref"), "note": r.get("origin_note")},
                        "status": r.get("status"), "code": r.get("code")})
                return self._json({"rows": rows, "version": f"SQLite v{cdb.DB_VERSION}",
                                   "stats": cdb.stats(os.environ.get("TENDEROPS_DB_PATH")),
                                   "sources": cdb.source_registry(os.environ.get("TENDEROPS_DB_PATH"))})
            except Exception as ex:
                return self._json({"error": f"costdb: {ex}"}, 500)
        m = u.path.split("/")
        if len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "lineage":
            tid, key = int(m[3]), urllib.parse.unquote(m[5])
            pack = _load_pack_for(tid)
            if pack is None:
                rec = _find_tender(tid)
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            try:
                cdb = _mod("costdb")
                conn = cdb.connect(os.environ.get("TENDEROPS_DB_PATH"))
                row = conn.execute(
                    "SELECT * FROM matches WHERE tender_id=? AND boq_key=? ORDER BY id DESC LIMIT 1",
                    (tid, key)).fetchone()
                why_res = _mod("project").resolve_why(os.environ.get("TENDEROPS_DB_PATH"), pack, boq_key=key)
                if not row:
                    conn.close()
                    if not why_res.get("error"):
                        return self._json({"match": None, "candidates": [], "chosen": None, "why": why_res})
                    return self._json({"error": f"няма записано решение за ключ '{key}' (преобработи поръчката)"}, 404)
                mrow = dict(row)
                cands = [dict(r) for r in conn.execute(
                    "SELECT item_id,rank,score,detail_json FROM match_candidates WHERE match_id=? ORDER BY rank",
                    (mrow["id"],))]
                chosen = None
                if mrow.get("chosen_item_id"):
                    ch = conn.execute("SELECT * FROM cost_items WHERE id=?", (mrow["chosen_item_id"],)).fetchone()
                    chosen = dict(ch) if ch else None
                    if chosen:
                        chosen.pop("extra_json", None)
                conn.close()
                return self._json({"match": mrow, "candidates": cands, "chosen": chosen, "why": why_res if not why_res.get("error") else None})
            except Exception as ex:
                return self._json({"error": str(ex)}, 500)
        if (len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "why") or \
           (len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "project" and m[5] == "why"):
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is None:
                rec = _find_tender(tid)
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            obj = (q.get("obj") or q.get("obj_id") or q.get("id") or [None])[0]
            key = (q.get("key") or q.get("boq_key") or [None])[0]
            if not obj and not key:
                return self._json({"error": "липсва параметър obj или key за lineage resolution"}, 400)
            try:
                res = _mod("project").resolve_why(
                    os.environ.get("TENDEROPS_DB_PATH"), pack, obj_id=obj, boq_key=key)
                if res.get("error"):
                    return self._json(res, 404)
                return self._json(res)
            except Exception as ex:
                return self._json({"error": f"why: {ex}"}, 500)
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "project":
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is None:
                rec = _find_tender(tid)
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            try:
                rec = _find_tender(tid) or {"id": tid}
                proj = _mod("project")
                dbp = os.environ.get("TENDEROPS_DB_PATH")
                graph = proj.get_project_graph(dbp, tid)
                if graph is None:
                    graph = proj.sync_project_graph(dbp, rec, pack)
                return self._json(graph)
            except Exception as ex:
                return self._json({"error": f"project: {ex}"}, 500)
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "georef":
            tid = int(m[3])
            rec = _find_tender(tid)
            dbp = os.environ.get("TENDEROPS_DB_PATH")
            res = _mod("project").get_georef(dbp, tid)
            if res is None:
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"tenderId": tid, "georef": None, "space": "LOCAL_PROJECT_SPACE"})
            return self._json(res)
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "spatial":
            # Spatial View Model: a read-only PROJECTION of the canonical pack.
            # No second source of truth, no writes (spec KIMI_K3_EN §24).
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is None:
                rec = _find_tender(tid)
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            sp = _mod("spatial")
            try:
                rec = _find_tender(tid) or {"id": tid}
                # keep the canonical Project Graph available for /project + /why
                proj = _mod("project")
                dbp = os.environ.get("TENDEROPS_DB_PATH")
                graph = proj.sync_project_graph(dbp, rec, pack)
                scope = (q.get("scope") or q.get("scope_id") or [None])[0]
                with _RUNNING_LOCK:
                    if tid in _SPATIAL:
                        return self._json({"error": "spatial построяването вече тече за тази поръчка"}, 409)
                    import threading as _th
                    ev = _th.Event()
                    _SPATIAL[tid] = ev
                try:
                    return self._json(sp.build_spatial_model(
                        rec, pack, scope_id=scope, graph=graph, cancel_cb=ev.is_set))
                finally:
                    with _RUNNING_LOCK:
                        _SPATIAL.pop(tid, None)
            except sp.SpatialCancelled as sc:
                return self._json({"ok": False, "cancelled": True, "error": str(sc)})
            except Exception as ex:
                return self._json({"error": f"spatial: {ex}"}, 500)
        if len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "spatial" and m[5] in ("geometry", "sources", "conflicts"):
            # Spatial v2 fused geometry evidence (contract §4/§6) — read-only pack view.
            # 404-shape identical to /spatial; evidence-absent = honest {"present": false}.
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is None:
                rec = _find_tender(tid)
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            ev = pack.get("geometry_evidence")
            what = m[5]
            if not isinstance(ev, dict) or not ev:
                if what == "geometry":
                    return self._json({"present": False})
                return self._json({"present": False, what: []})
            if what == "geometry":
                return self._json({"present": True, **ev})
            return self._json({"present": True, what: ev.get(what) or []})
        if len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "spatial" and m[5] == "object":
            # Single SpatialObject from the compiled scene_v2, built on demand via the
            # same read-projection flow as /spatial (pack + project graph sync).
            tid = int(m[3])
            sid = (q.get("spatial_id") or [""])[0]
            if not sid:
                return self._json({"error": "липсва параметър spatial_id"}, 400)
            pack = _load_pack_for(tid)
            if pack is None:
                rec = _find_tender(tid)
                if not rec:
                    return self._json({"error": f"поръчка {tid} не е намерена"}, 404)
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            try:
                rec = _find_tender(tid) or {"id": tid}
                proj = _mod("project")
                dbp = os.environ.get("TENDEROPS_DB_PATH")
                graph = proj.sync_project_graph(dbp, rec, pack)
                scope = (q.get("scope") or q.get("scope_id") or [None])[0]
                model = _mod("spatial").build_spatial_model(rec, pack, scope_id=scope, graph=graph)
                scene = model.get("scene_v2") or {}
                obj = next((o for o in (scene.get("objects") or []) if isinstance(o, dict) and o.get("id") == sid), None)
                if obj is None:
                    return self._json({"error": f"няма обект '{sid}' в scene_v2 (няма геометрични доказателства или сцената не е компилирана)"}, 404)
                return self._json(obj)
            except Exception as ex:
                return self._json({"error": f"spatial object: {ex}"}, 500)
        if len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "spatial" and m[5] == "glb":
            # Content-addressed GLB cache: data/geometry_cache/<sha256>.glb (strict key).
            key = (q.get("key") or [""])[0].strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                return self._json({"error": "key трябва да е точно 64 hex символа (sha256)"}, 400)
            fpath = _geometry_cache_dir() / f"{key}.glb"
            if not fpath.is_file():
                return self._json({"error": "няма кеширан GLB с този ключ — преобработи с геометричен източник"}, 404)
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "model/gltf-binary")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "offer.docx":
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is None:
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            guard = _generation_guard(self, tid, pack)
            if guard:
                return self._json(guard, 409)
            rec = _find_tender(tid)
            if not rec:
                return self._json({"error": "поръчката не е в регистъра"}, 404)
            res = _mod("offer").build_offer(rec, pack, PROCESSED, PROCESSED, firm=settings().get("firm"))
            if res.get("error"):
                return self._json({"error": res["error"]}, 500)
            body = Path(res["path"]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", f"attachment; filename=oferta_{tid}.docx")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "submission.zip":
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is None:
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            guard = _generation_guard(self, tid, pack)
            if guard:
                return self._json(guard, 409)
            rec = _find_tender(tid)
            if not rec:
                return self._json({"error": "поръчката не е в регистъра"}, 404)
            try:
                sub_mod = _mod("submission")
                res = sub_mod.build_submission(rec, pack, PROCESSED, PROCESSED, firm=settings().get("firm"))
                zip_path = Path(res.get("zip") or (PROCESSED / f"submission_{tid}.zip"))
                if not zip_path.exists():
                    return self._json({"error": "архивът не бе намерен след генериране"}, 500)
                body = zip_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f"attachment; filename=submission_{tid}.zip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as ex:
                return self._json({"error": f"submission zip fail: {ex}"}, 500)
        if len(m) == 4 and m[1] == "api" and m[2] == "tender" and m[3].isdigit():
            tid = int(m[3])
            pack = _load_pack_for(tid)
            if pack is not None:
                pack["resolutions"] = resolutions(tid)
                return self._json(pack)
            return self._json({"error": "няма обработен пакет — стартирай „Обработи“"}, 404)
        if u.path == "/api/agent/tools":
            tools = _mod("ai_tools").TOOLS
            return self._json({"count": len(tools),
                               "tools": [{"name": t["function"]["name"],
                                          "description": t["function"]["description"],
                                          "mutating": t["function"]["description"].startswith("MUTATING")}
                                         for t in tools]})
        if u.path == "/api/agent/models":
            s = settings().get("llm", {})
            try:
                llm = _mod("llm")
                r = llm.list_models(s.get("base_url"), api_key=s.get("api_key") or None)
                ids = []
                for m0 in (r.get("data") or []):
                    for cand in [m0.get("id")] + list(m0.get("aliases") or []):
                        if cand and cand not in ids:
                            ids.append(cand)
                return self._json({"ok": True, "models": ids})
            except Exception as ex:
                return self._json({"ok": False, "error": str(ex)}, 502)
        if u.path == "/api/agent/note":
            p = DEMO / "agent_note.md"
            txt = p.read_text(encoding="utf-8") if p.exists() else ""
            return self._json({"ok": True, "path": str(p), "content": txt})
        if u.path == "/api/estimation/scan":
            man = DEMO / "estimation_inbox" / "manifest.json"
            manifest = json.loads(man.read_text(encoding="utf-8-sig")) if man.exists() else {"files": []}
            return self._json({"ok": True, **manifest})
        if u.path == "/api/estimation/history":
            # Историята е библиотеката от пакети в инбокса — те оцеляват clear.
            inbox = DEMO / "estimation_inbox"
            hist = []
            if inbox.exists():
                for p in inbox.glob("estimation_EST*.json"):
                    try:
                        pack = json.loads(p.read_text(encoding="utf-8-sig"))
                    except Exception:
                        continue  # нечетим/прекъснат пакет — историята го пропуска тихо
                    hist.append({"id": pack.get("tenderId") or p.stem[len("estimation_"):],
                                 "generatedAt": pack.get("generatedAt") or "",
                                 "rows": len(pack.get("boq") or []),
                                 "totalExclVat": (pack.get("pricing") or {}).get("totalExclVat"),
                                 "name": pack.get("name") or pack.get("tenderId") or p.stem})
            hist.sort(key=lambda h: (h["generatedAt"], h["id"]), reverse=True)
            return self._json({"ok": True, "history": hist[:50]})
        if u.path == "/api/estimation/capabilities":
            # лек always-200 маркер на новия backend — UI probe без 4xx console шум
            return self._json({"ok": True, "spatial": True, "cancel": True,
                               "bulkPrice": True, "hardReset": True, "schema": "2026-09-08"})
        if u.path == "/api/estimation/spatial":
            # Estimation door's own project view: same read-only spatial projection,
            # but over the estimation pack (EST id space is disjoint from tenders —
            # тръбите не се пипат: no registry, no project_graph sync).
            est = str((q.get("id") or [""])[0])
            if not re.match(r"^EST\d{8}_\d{6}[a-z]?$", est):
                return self._json({"error": "невалиден estimation id"}, 400)
            ppath = DEMO / "estimation_inbox" / f"estimation_{est}.json"
            if not ppath.exists():
                return self._json({"error": "няма такава оценка — първо пусни run"}, 404)
            try:
                pack = json.loads(ppath.read_text(encoding="utf-8-sig"))
            except Exception:
                return self._json({"error": f"пакетът {ppath.name} е нечетим"}, 500)
            if not (pack.get("boq") or []):
                return self._json({"error": "оценката няма количествени редове — няма какво да се визуализира"}, 404)
            sp = _mod("spatial")
            try:
                rec = {"id": est, "name": f"частен проект {est}", "buyer": None, "number": est}
                return self._json(sp.build_spatial_model(rec, pack))
            except Exception as ex:
                return self._json({"error": f"spatial: {ex}"}, 500)
        if u.path == "/api/estimation/download":
            fname = Path(str((q.get("file") or [""])[0])).name  # basename only — no traversal
            fpath = (DEMO / "estimation_inbox" / fname)
            if not fname or not fpath.exists() or not fname.startswith(("KSS_ocenena_", "estimation_")):
                return self._json({"error": "файлът не съществува или не е estimation артефакт"}, 404)
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                             if fname.endswith(".xlsx") else "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f"attachment; filename={fname}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        global _EST_RUNNING, _EST_CANCEL
        self._req_t0 = time.time()
        self._corr_id = self._get_corr_id()
        if telemetry:
            try:
                telemetry.log_event(
                    telemetry.EventType.API_REQUEST,
                    message=f"POST {self.path}",
                    severity=telemetry.Severity.INFO,
                    route=self.path,
                    operation="POST",
                    correlation_id=self._corr_id,
                    session_id=self._get_session_id(),
                    component="server"
                )
            except Exception:
                pass
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/api/telemetry/events", "/api/telemetry/event"):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                return self._json({"error": "bad json"}, 400)
            items = body if isinstance(body, list) else (body.get("events") if isinstance(body.get("events"), list) else [body])
            count = 0
            if telemetry:
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    telemetry.log_event(
                        event_type=it.get("event_type") or it.get("type") or telemetry.EventType.UI_ANALYSIS_STARTED,
                        message=it.get("message") or it.get("detail"),
                        severity=it.get("severity") or telemetry.Severity.INFO,
                        module=it.get("module") or "ui",
                        tender_id=it.get("tender_id") or it.get("tenderId"),
                        run_id=it.get("run_id") or it.get("runId"),
                        session_id=it.get("session_id") or self._get_session_id(),
                        correlation_id=it.get("correlation_id") or self._corr_id,
                        component="frontend",
                        actor_type="user",
                        payload=it.get("payload") or it
                    )
                    count += 1
            return self._json({"ok": True, "received": count})
        m = u.path.split("/")
        if len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "process" and m[5] == "cancel":
            tid = int(m[3])
            with _RUNNING_LOCK:
                ev = _RUNNING.get(tid)
            if not ev:
                return self._json({"error": "няма течаща обработка за тази поръчка"}, 409)
            ev.set()
            return self._json({"ok": True, "cancelled": tid})
        if u.path == "/api/tenders/add":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            ref = str(body.get("ref", "")).strip()
            try:
                from . import eop as _eop
            except Exception:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location("eop", str(Path(__file__).resolve().parent / "eop.py"))
                _eop = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_eop)
            tid = _eop.parse_tender_ref(ref)
            if not tid:
                return self._json({"error": "неразпознат URL/номер; приема: 601701 или https://app.eop.bg/today/601701"}, 400)
            try:
                rec = _eop.fetch_tender(tid)
            except Exception as ex:
                return self._json({"error": f"ЦАИС ЕОП недостъпен или поръчката не е публична: {ex}"}, 502)
            reg = registry()
            reg["tenders"] = [t for t in reg.get("tenders", []) if t.get("id") != tid]
            reg["tenders"].append(rec)
            registry_save(reg)
            selected = workflow.register_tender(os.environ['TENDEROPS_DB_PATH'], rec)
            return self._json({"ok": True, "tender": selected})
        if u.path == "/api/settings/llm":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            s = settings()
            s.setdefault("llm", {})
            if "base_url" in body: s["llm"]["base_url"] = str(body["base_url"]).strip()
            if "model" in body: s["llm"]["model"] = str(body["model"]).strip()
            if "api_key" in body:
                k = str(body["api_key"]).strip()
                if k != "__keep__":
                    s["llm"]["api_key"] = k
            settings_save(s)
            return self._json({"ok": True})
        if u.path == "/api/settings/llm/test":
            length = int(self.headers.get("Content-Length", 0))
            body = {}
            if length:
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    body = {}
            saved = settings().get("llm", {})
            # предимство: стойностите от ФОРМАТА (това което потребителят вижда);
            # празното ключ-поле означава „ползвай записания ключ"
            base = str(body.get("base_url") or saved.get("base_url") or "").strip()
            model = str(body.get("model") or saved.get("model") or "").strip()
            k = str(body.get("api_key", "")).strip()
            key = saved.get("api_key") if k in ("", "__keep__") else k
            try:
                import importlib.util as _ilu
                _spec = _ilu.spec_from_file_location("llm", str(Path(__file__).resolve().parent / "llm.py"))
                _llm = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_llm)
                ok, note, resolved = _llm.test_conn(base, model, api_key=key or None)
            except Exception as ex:
                ok, note, resolved = False, str(ex), None
            return self._json({"ok": bool(ok), "note": note, "resolved_model": resolved,
                               "base_url": base, "model": model})
        m = u.path.split("/")
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "process":
            tid = int(m[3])
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                body = {}
            rec = _find_tender(tid)
            if not rec:
                return self._json({"error": f"поръчка {tid} не е в регистъра — първо „＋ нова“"}, 404)
            # кешът важи само ако: пакетът е валиден JSON, има редове, cost env същата,
            # И входните документи не са се сменили (filesSig). Счупен пакет → карантина.
            if not body.get("force"):
                p = PROCESSED / f"{tid}.json"
                if p.exists():
                    try:
                        pk = json.loads(p.read_text(encoding="utf-8-sig"))
                    except Exception:
                        # B1: счупен/отрязан пакет не бива да блокира поръчката завинаги —
                        # карантина (запазва се за одит, не се трие), кеш = няма.
                        pk = None
                        try:
                            bad = PROCESSED / f"{tid}.corrupt.json"
                            if bad.exists():
                                bad = PROCESSED / f"{tid}.corrupt.{int(time.time())}.json"
                            p.rename(bad)
                            print(f"[server] pack {p.name} unreadable -> quarantined as {bad.name}")
                        except OSError as qex:
                            print(f"[server] pack quarantine failed: {qex}")
                        try:
                            if telemetry:
                                telemetry.log_event("PACK_QUARANTINED",
                                                    message=f"Corrupt pack quarantined: {p.name}",
                                                    tender_id=tid, component="server", severity="WARN",
                                                    db_path=os.environ.get("TENDEROPS_DB_PATH"))
                        except Exception:
                            pass
                    if pk is not None:
                        if not (pk.get("boq") or []):
                            pk = None  # празният пакет не е пакет — никога не кешираме празнини
                        else:
                            fdir = PROCESSED / "files" / str(tid)
                            sig_now = sorted(f.name for f in fdir.glob("*") if f.is_file()) if fdir.exists() else []
                            if pk.get("filesSig") is None and sig_now:
                                pk = None  # стар пакет без сигнатура, но файлове присъстват — преработи веднъж
                            elif pk.get("filesSig") is not None and pk.get("filesSig") != sig_now:
                                pk = None  # документите се смениха след този пакет
                    env_now = None
                    if pk is not None:
                        try:
                            env_now = _mod("costdb").current_env_id(os.environ.get("TENDEROPS_DB_PATH"))
                        except Exception:
                            pass
                    if pk is not None and env_now is not None and pk.get("pricing", {}).get("costEnvVersion") == env_now:
                        return self._json({"ok": True, "cached": True, "env": env_now, "tenderId": tid,
                                           "rows": len(pk["boq"]),
                                           "priced": sum(1 for l in pk["boq"] if l["rule"] == "CSV"),
                                           "totalExclVat": pk["pricing"]["totalExclVat"],
                                           "kss": pk["pipe"].get("kss"),
                                           "llmOk": bool(pk.get("llm", {}).get("ok"))})
            import threading as _th
            with _RUNNING_LOCK:
                if tid in _RUNNING:
                    return self._json({"error": "обработката вече тече — изчакай или я Спри"}, 409)
                ev = _th.Event()
                _RUNNING[tid] = ev
            try:
                pipe = _mod("pipeline")
            except Exception as ex:
                with _RUNNING_LOCK:
                    _RUNNING.pop(tid, None)
                return self._json({"error": f"pipeline недостъпен: {ex}"}, 500)
            try:
                res = pipe.process_tender(rec, DEMO, PROCESSED, llm_settings=settings().get("llm"),
                                          cancel_cb=ev.is_set)
            finally:
                with _RUNNING_LOCK:
                    _RUNNING.pop(tid, None)
            if res.get("cancelled"):
                return self._json({"ok": False, "cancelled": True, "error": res.get("error")}, 200)
            if not res.get("ok"):
                if res.get("code") == "NO_SCOPE":
                    # нормален случай (тендер без КСС) — навигиращ отговор, не крах
                    return self._json({"ok": False, "code": "NO_SCOPE", "tenderId": tid,
                                       "message": res.get("error"),
                                       "guidance": res.get("guidance") or [],
                                       "state": res.get("state")})
                return self._json({"error": f"обработката се провали: {res.get('error')}", "state": res.get("state")}, 500)
            pack = res["pack"]
            return self._json({"ok": True, "tenderId": tid,
                               "rows": len(pack["boq"]),
                               "priced": sum(1 for l in pack["boq"] if l["rule"] == "CSV"),
                               "totalExclVat": pack["pricing"]["totalExclVat"],
                               "kss": pack["pipe"].get("kss"), "llmOk": bool(pack.get("llm", {}).get("ok"))})
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "georef":
            tid = int(m[3])
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                return self._json({"error": "bad json"}, 400)
            res = _mod("project").set_georef(
                os.environ.get("TENDEROPS_DB_PATH"), tid,
                body.get("lat"), body.get("lon"),
                source=str(body.get("source") or "operator"),
                note=str(body.get("note") or ""))
            if res.get("error"):
                return self._json({"error": res["error"]}, 400)
            return self._json(res)
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "upload_prices":
            tid = int(m[3])
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return self._json({"error": "празен файл"}, 400)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fname = str((qs.get("filename") or ["upload.bin"])[0]).strip()
            safe_name = re.sub(r'[\\/:*?"<>|]+', "_", fname)[:120] if fname else "upload.bin"
            payload = self.rfile.read(length)
            pipe = _mod("pipeline")
            cdb = _mod("costdb")
            head = payload[:8]
            is_xlsx = head[:4] == b"PK\x03\x04"
            is_xls = head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
            ext = Path(safe_name).suffix.lower()
            rows = []
            is_kss_shape = False
            scope_doc_id = None
            scope_rows_n = 0
            tmp_dir = PROCESSED / "files" / str(tid)
            tmp_dir.mkdir(parents=True, exist_ok=True)
            fpath = tmp_dir / ("user_upload_" + safe_name)
            fpath.write_bytes(payload)
            try:
                if is_xlsx or is_xls or ext in (".xlsx", ".xls"):
                    kss_rows = pipe.parse_kss_priced(fpath)
                    if kss_rows:
                        # КСС-образeн файл = ОБХВАТ на поръчката (първокласен документ) +
                        # единичните цени в него са фирмени (account-priority), никога тотали като „цени".
                        is_kss_shape = True
                        scope_rows_n = len(kss_rows)
                        rows = [{"desc": r["desc"], "unit": r.get("unit") or "бр",
                                 "qty": r["qty"], "price": r["own_price"]}
                                for r in kss_rows if r.get("own_price")]
                    else:
                        rows = pipe.parse_price_sheet(fpath)
                elif ext == ".docx" or head[:2] == b"PK":
                    rows = pipe.parse_price_sheet(fpath)
                elif ext in (".json", ".csv", ".txt"):
                    txt = payload.decode("utf-8", "replace")
                    if ext == ".json":
                        j = json.loads(txt)
                        for it in (j if isinstance(j, list) else j.get("rows") or []):
                            d = str(it.get("desc") or it.get("name") or "").strip()
                            amt = it.get("amount") or it.get("price") or (it.get("money") or {}).get("amount")
                            if d and isinstance(amt, (int, float)) and amt > 0:
                                rows.append({"desc": d, "unit": str(it.get("unit") or "бр"),
                                             "qty": float(it.get("qty") or 1.0), "price": float(amt)})
                    else:
                        for line in txt.splitlines():
                            parts = line.replace(";", "|").split("|") if ";" in line else line.split(",")
                            if len(parts) >= 3:
                                d = parts[0].strip()
                                try:
                                    amt = float(parts[-1].strip().replace(",", "."))
                                except Exception:
                                    amt = None
                                if d and amt and amt > 0:
                                    rows.append({"desc": d, "unit": parts[-2].strip() or "бр", "qty": 1.0, "price": float(amt)})
                else:
                    return self._json({"error": f"форматът не се разчита: {ext or safe_name}"}, 422)
            except Exception as ex:
                return self._json({"error": f"parse грешка: {ex}"}, 422)
            if is_kss_shape:
                rec = _find_tender(tid)
                if rec:
                    import hashlib as _hl
                    did = "upl" + _hl.sha1(payload).hexdigest()[:12]
                    doc = {"name": f"{Path(safe_name).stem} [качена КСС]{ext or '.xlsx'}", "docId": did,
                           "ext": ext or ".xlsx", "size": len(payload), "modified": None,
                           "localPath": str(fpath)}
                    reg = registry()
                    for t in reg.get("tenders", []):
                        if t.get("id") == tid:
                            docs = [d for d in (t.get("documents") or []) if d.get("docId") != did]
                            docs.append(doc)
                            t["documents"] = docs
                            break
                    registry_save(reg)
                    rec_wf = next((x for x in reg.get("tenders", []) if x.get("id") == tid), None)
                    if rec_wf:
                        try:
                            workflow.register_tender(os.environ["TENDEROPS_DB_PATH"], rec_wf)
                        except Exception:
                            pass
                    scope_doc_id = did
                if not rows:
                    # празна КСС матрица: обхватът е прикачен, в справочника не влиза нищо
                    return self._json({"ok": True, "file": safe_name, "parsedRows": 0,
                                       "scopeAttached": bool(scope_doc_id), "scopeRows": scope_rows_n,
                                       "matchedEst": 0, "statusApplied": "scope-only",
                                       "note": "КСС обхватът е прикачен към поръчката; ценова колона е празна — "
                                               "нищо не влиза в справочника. Стартирай обработката наново."})
            if not rows:
                return self._json({"error": "няма редове с реална цена — нищо не се вкарва"}, 422)
            items, today = [], time.strftime("%Y-%m-%d")
            # Съдържание реши, не името: реални ЕДИНИЧНИ цени → активен фирмен слой (account
            # priority, owner 2026-09-06); тотали/празни колони никога не стават „цени".
            applied_status = "active"
            for r in rows:
                amt = r.get("price")
                if amt is None:
                    continue
                items.append({"desc": r["desc"], "unit": r.get("unit") or "бр",
                              "money": {"amount": float(amt), "currency": "EUR", "vatIncluded": False, "asOf": today},
                              "origin": {"kind": "user_upload", "ref": safe_name,
                                         "note": f"качено от потребителя към поръчка {tid} на {today}"},
                              "source": f"user:{safe_name}", "status": applied_status})
            if not items:
                return self._json({"error": "парсирането намира позиции, но без цени — нула вкарано"}, 422)
            seed = DEMO / f"costdb_seed_user_{tid}_{Path(safe_name).stem.lower()}.json"
            seed.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            sync = cdb.sync_json_sources(DEMO, os.environ.get("TENDEROPS_DB_PATH"))
            proc = PROCESSED / f"{tid}.json"
            bound = 0
            if proc.exists():
                pack = json.loads(proc.read_text(encoding="utf-8-sig"))
                indexed = {((i.get("desc") or "").strip().lower(), (i.get("unit") or "").strip()): i for i in items}
                for l in pack.get("boq", []):
                    if l.get("rule") != "EST":
                        continue
                    k = ((l.get("desc") or "").strip().lower(), (l.get("unit") or "").strip())
                    hit = indexed.get(k)
                    if hit:
                        cdb.upsert_resolution(os.environ.get("TENDEROPS_DB_PATH"), tid, l["key"],
                                              note=f"собствена цена {hit['money']['amount']} €/{hit.get('unit','бр')} от „{safe_name}“",
                                              actor="user", rationale="потребителски ценови лист",
                                              evidence={"uploadedPriceEur": hit["money"]["amount"],
                                                        "unit": hit.get("unit"), "file": safe_name})
                        bound += 1
            return self._json({"ok": True, "file": safe_name, "parsedRows": len(rows),
                               "savedAs": seed.name, "matchedEst": bound, "dbSync": sync.get("added", 0),
                               "statusApplied": applied_status,
                               "scopeAttached": bool(scope_doc_id), "scopeRows": scope_rows_n})
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "attach":
            # Операторски канал: прикачване на ЦАИС „Експорт" файл като ПЪРВОКЛАСЕН документ
            # на поръчката (персистира в регистъра + files/<tid>/); pipeline.localPath го подбира.
            tid = int(m[3])
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return self._json({"error": "празен файл"}, 400)
            rec = _find_tender(tid)
            if not rec:
                return self._json({"error": f"поръчка {tid} не е в регистъра — първо „＋ нова“"}, 404)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fname = str((qs.get("filename") or ["export.bin"])[0]).strip()
            ext = Path(fname).suffix.lower()
            if ext not in (".xls", ".xlsx", ".docx", ".pdf", ".zip", ".dxf", ".ifc"):
                return self._json({"error": f"неподдържан формат за експорт: {ext or fname}"}, 422)
            payload = self.rfile.read(length)
            if len(payload) > 30 * 1024 * 1024:
                return self._json({"error": "над 30 MB лимита"}, 413)
            import hashlib as _hl
            safe_name = re.sub(r'[\\/:*?"<>|]+', "_", fname)[:120]
            did = "exp" + _hl.sha1(payload).hexdigest()[:12]
            fdir = PROCESSED / "files" / str(tid)
            fdir.mkdir(parents=True, exist_ok=True)
            dst = fdir / f"{did}_{safe_name}"
            dst.write_bytes(payload)
            doc = {"name": f"{Path(safe_name).stem} [локален експорт]{ext}", "docId": did,
                   "ext": ext, "size": len(payload), "modified": None, "localPath": str(dst)}
            reg = registry()
            merged = False
            for t in reg.get("tenders", []):
                if t.get("id") == tid:
                    docs = [d for d in (t.get("documents") or []) if d.get("docId") != did]
                    docs.append(doc)
                    t["documents"] = docs
                    merged = True
                    break
            if not merged:
                return self._json({"error": f"поръчка {tid} липсва в registry.json (desync)"}, 409)
            registry_save(reg)
            # workflow store holds its own copy — refresh it so the pipeline sees attached docs
            rec_wf = next((x for x in reg.get("tenders", []) if x.get("id") == tid), None)
            if rec_wf:
                try:
                    workflow.register_tender(os.environ["TENDEROPS_DB_PATH"], rec_wf)
                except Exception:
                    pass
            if telemetry:
                try:
                    telemetry.log_event(
                        telemetry.EventType.TENDER_UPDATED,
                        message=f"Local export attached to tender {tid}: {safe_name} ({len(payload)//1024} KB)",
                        tender_id=tid, component="server", actor_type="user",
                        payload={"file": safe_name, "docId": did},
                        db_path=os.environ.get("TENDEROPS_DB_PATH"))
                except Exception:
                    pass
            return self._json({"ok": True, "tenderId": tid, "docId": did, "savedAs": dst.name,
                               "documentsNow": len(doc)})
        if u.path == "/api/estimation/cancel":
            # Cooperative stop of the single-flight estimation run (canon: loud, scoped).
            with _RUNNING_LOCK:
                ev = _EST_CANCEL
            if ev is None:
                return self._json({"error": "няма течаща оценка"}, 409)
            ev.set()
            return self._json({"ok": True, "cancelled": "estimation-run"})
        if u.path == "/api/estimation/resolve_candidates":
            # Bulk HUMAN DOOR: operator decides to price all open gaps from the corpus'
            # own candidates. Prices come ONLY from real cost_items rows (never invented);
            # rows without a candidate stay open, loudly.
            with _RUNNING_LOCK:
                if _EST_RUNNING:
                    return self._json({"error": "оценката тече — първо я Спри"}, 409)
            inbox = DEMO / "estimation_inbox"
            packs = sorted(inbox.glob("estimation_EST*.json"),
                           key=lambda p: p.stat().st_mtime) if inbox.exists() else []
            if not packs:
                return self._json({"error": "няма estimation run — първо пусни оценка"}, 404)
            try:
                pack = json.loads(packs[-1].read_text(encoding="utf-8-sig"))
            except Exception:
                return self._json({"error": f"пакетът {packs[-1].name} е нечетим"}, 500)
            cdb = _mod("costdb")
            dbp = os.environ.get("TENDEROPS_DB_PATH")
            mp_path = _estimation_prices_path()
            store = {}
            if mp_path.exists():
                try:
                    store = json.loads(mp_path.read_text(encoding="utf-8-sig")) or {}
                except Exception:
                    store = {}
            priced, still_open = [], []
            for l in pack.get("boq", []):
                if l.get("rule") != "EST":
                    continue
                cand = ((l.get("match") or {}).get("top_candidates") or [None])[0]
                price = _candidate_price(cdb, dbp, (cand or {}).get("id")) if cand else None
                mkey = (l.get("desc") or "").strip().lower() + " ∥ " + (l.get("unit") or "бр").strip()
                if price is None:
                    still_open.append(l.get("key"))
                    continue
                note = f"кандидат {cand.get('ref') or cand.get('desc') or ''} · score {cand.get('score')}"
                store[mkey] = {"price": price, "unit": l.get("unit") or "бр", "desc": l.get("desc"),
                               "note": note, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "actor": "operator:bulk"}
                priced.append(l.get("key"))
                try:  # одиторска следа — същият ledger като единичния resolve
                    save_resolution(f"est::{mkey}", note, "operator")
                except Exception as ex:
                    print(f"est bulk resolution audit fail: {ex}")
            mp_path.parent.mkdir(parents=True, exist_ok=True)
            mp_path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
            if telemetry:
                try:
                    telemetry.log_event("ESTIMATION_BULK_PRICE",
                                        message=f"Bulk candidate pricing: {len(priced)} priced, {len(still_open)} still open",
                                        component="server", actor_type="user",
                                        payload={"priced": priced, "stillOpen": still_open}, db_path=dbp)
                except Exception:
                    pass
            return self._json({"ok": True, "priced": priced, "stillOpen": still_open,
                               "applyNote": "цените влизат със следващия run — пусни оценката отново"})
        if u.path == "/api/admin/reset":
            # HARD RESET на работната сесия: чисти всичко роботно, пази историята/одита.
            # ПАЗИ (канон): costdb корпус, registry.json, human_resolutions/resolutions.json,
            # runs/run_events/matches/outcomes (история), snapshots, settings, workflow идентичности.
            with _RUNNING_LOCK:
                for ev in list(_RUNNING.values()):
                    ev.set()
                for ev in list(_SPATIAL.values()):
                    ev.set()
                ev = _EST_CANCEL
                if ev is not None:
                    ev.set()
            time.sleep(0.3)
            with _RUNNING_LOCK:
                if _EST_RUNNING or _RUNNING:
                    return self._json({"error": "операции още не са спрели — опитай пак след секунда"}, 409)
                _RUNNING.clear(); _SPATIAL.clear()
            removed = {}
            inbox = DEMO / "estimation_inbox"
            n = 0
            if inbox.exists():
                for p in inbox.glob("*"):
                    if p.is_file():
                        try:
                            p.unlink(); n += 1
                        except OSError:
                            pass
                (inbox / "manifest.json").write_text(json.dumps({"files": []}, ensure_ascii=False), encoding="utf-8")
            removed["estimationInbox"] = n
            n = 0
            if PROCESSED.exists():
                for p in PROCESSED.glob("*"):
                    if p.is_file() and (p.suffix == ".json" or p.name.endswith(".zip")):
                        try:
                            p.unlink(); n += 1
                        except OSError:
                            pass
                for d in PROCESSED.glob("submission_*"):
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True); n += 1
            removed["processed"] = n
            body = {}
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                body = {}
            if not body.get("keepDownloads", True):
                fdir = PROCESSED / "files"
                if fdir.exists():
                    shutil.rmtree(fdir, ignore_errors=True)
                    removed["downloadCache"] = "files/"
            cdb = _mod("costdb")
            removed["zombieRunsClosed"] = cdb.sweep_interrupted_runs(
                os.environ.get("TENDEROPS_DB_PATH"), note="hard reset")
            _cache.clear()
            if telemetry:
                try:
                    telemetry.log_event("ADMIN_HARD_RESET", message=f"Hard reset: {removed}",
                                        component="server", actor_type="user", severity="WARN",
                                        payload=removed, db_path=os.environ.get("TENDEROPS_DB_PATH"))
                except Exception:
                    pass
            return self._json({"ok": True, "removed": removed,
                               "preserved": ["costdb corpus", "registry", "resolutions/audit",
                                             "runs/history", "snapshots", "settings", "identities"]})
        if u.path == "/api/estimation/clear":            # инбоксът е текуща evidence сесия — чисти се изрично между проекти
            with _RUNNING_LOCK:
                if _EST_RUNNING:
                    return self._json({"error": "оценката тече — първо я Спри, после чисти"}, 409)
            inbox = DEMO / "estimation_inbox"
            removed = 0
            if inbox.exists():
                for p in inbox.glob("*"):
                    # clear чисти само НЕизпълнената партида — пакетите/оценените КСС
                    # и runs/<est_id>/ остават: те вече са историята (GET .../history)
                    if p.is_file() and not _is_estimation_artifact(p.name):
                        try:
                            p.unlink()
                            removed += 1
                        except OSError as ex:  # Windows lock etc. — гласно, не фатално
                            print(f"[server] clear skip (locked): {p.name}: {ex}")
                names = {p.name for p in inbox.glob("*") if p.is_file()}
                man = inbox / "manifest.json"
                manifest = json.loads(man.read_text(encoding="utf-8-sig")) if man.exists() else {"files": []}
                manifest["files"] = [r for r in manifest.get("files") or [] if r.get("saved") in names]
                man.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            if telemetry:
                try:
                    telemetry.log_event("ESTIMATION_INBOX_CLEARED", message=f"Estimation inbox cleared: {removed} files",
                                        component="server", actor_type="user",
                                        db_path=os.environ.get("TENDEROPS_DB_PATH"))
                except Exception:
                    pass
            return self._json({"ok": True, "removed": removed})
        if u.path == "/api/estimation/open":
            # Double-click in the UI → open the evidence file in its NATIVE app (Excel/PDF/Word).
            # Only files inside the estimation inbox, only read-worthy extensions, no traversal.
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            fname = Path(str(body.get("file") or "")).name
            if not fname or _is_estimation_artifact(fname):
                return self._json({"error": "файлът не е evidence на инбокса"}, 404)
            if Path(fname).suffix.lower() not in (".xls", ".xlsx", ".pdf", ".docx", ".doc", ".txt", ".json", ".csv", ".png", ".jpg", ".jpeg"):
                return self._json({"error": "неподдържан формат за отваряне"}, 422)
            fpath = DEMO / "estimation_inbox" / fname
            if not fpath.exists():
                # legacy manifest rows predate 'saved': find by original-name suffix
                hit = next((p for p in sorted((DEMO / "estimation_inbox").glob(f"*_{fname}")) if p.is_file()), None)
                if not hit:
                    return self._json({"error": "файлът го няма в инбокса"}, 404)
                fpath = hit
            try:
                if sys.platform.startswith("win"):
                    os.startfile(str(fpath))  # native association on the user's machine
                elif sys.platform == "darwin":
                    __import__("subprocess").Popen(["open", str(fpath)])
                else:
                    __import__("subprocess").Popen(["xdg-open", str(fpath)])
                return self._json({"ok": True, "opened": fname})
            except Exception as ex:
                return self._json({"error": f"отварянето не стана: {ex}"}, 500)
        if u.path == "/api/estimation/scan":
            # Cost Estimation intake, honest version: files the user drops are parsed for real
            # and the aggregate says WHAT was understood — never a fake spatial promise.
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return self._json({"error": "празен файл"}, 400)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            fname = str((qs.get("filename") or ["scan.bin"])[0]).strip()
            safe_name = re.sub(r'[\\/:*?"<>|]+', "_", fname)[:120]
            payload = self.rfile.read(length)
            if len(payload) > 30 * 1024 * 1024:
                return self._json({"error": "над 30 MB лимита"}, 413)
            inbox = DEMO / "estimation_inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            dst = inbox / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_name}"
            dst.write_bytes(payload)
            pipe = _mod("pipeline")
            ext = Path(safe_name).suffix.lower()
            record = {"file": safe_name, "size": len(payload), "ext": ext, "saved": dst.name,
                      "at": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": 0, "status": "прочетен"}
            try:
                if ext in (".xlsx", ".xls"):
                    rows = pipe.parse_kss(dst)
                    if not rows:
                        rows = pipe.parse_price_sheet(dst)
                        record["parsedAs"] = "price-sheet"
                    else:
                        record["parsedAs"] = "kss"
                    record["rows"] = len(rows)
                    record["sections"] = sorted({r.get("section") or "" for r in rows if r.get("section")})[:12]
                elif ext == ".docx":
                    rows = pipe.parse_kss_docx(dst)
                    record["parsedAs"] = "docx-tables"
                    record["rows"] = len(rows)
                elif ext == ".pdf":
                    try:
                        rows = pipe.parse_kss_pdf(dst)
                        record["parsedAs"] = "pdf-text"
                        record["rows"] = len(rows)
                    except ModuleNotFoundError:
                        record["status"] = "PDF парсърът не е в тази среда (pymupdf липсва)"
                        record["parsedAs"] = "pdf-unparsed"
                elif ext in (".dxf", ".ifc"):
                    # Cost Estimation owns Spatial: parse CAD/BIM NOW during Evidence Scan.
                    # Full extraction is cached by content hash; the UI/manifest gets only
                    # a compact summary of what was actually understood from this file.
                    record["parsedAs"] = "geometry-source"
                    geom = _mod("spatial_geometry")
                    cache_dir = DEMO.parent / "geometry_cache"
                    extracts = geom.cached_extract([dst], cache_dir)
                    gres = extracts[0] if extracts else {}

                    fp = gres.get("footprint")
                    has_primary = bool(isinstance(fp, dict) and isinstance(fp.get("value"), dict)
                                       and isinstance(fp["value"].get("polygon_m"), list)
                                       and len(fp["value"]["polygon_m"]) >= 3)
                    extras = gres.get("extra_footprints")
                    extra_values = (extras.get("value") or []) if isinstance(extras, dict) else []
                    extra_buildings = sum(1 for x in extra_values
                                          if isinstance(x, dict) and isinstance(x.get("polygon_m"), list)
                                          and len(x["polygon_m"]) >= 3)
                    site = gres.get("site_boundary")
                    has_site = bool(isinstance(site, dict) and isinstance(site.get("value"), dict)
                                    and isinstance(site["value"].get("polygon_m"), list)
                                    and len(site["value"]["polygon_m"]) >= 3)
                    nets = gres.get("networks")
                    net_values = (nets.get("value") or []) if isinstance(nets, dict) else []
                    dims = gres.get("dimensions") if isinstance(gres.get("dimensions"), dict) else {}
                    dim_values = {}
                    for k in ("length_m", "width_m", "height_m", "floor_height_m", "storeys"):
                        fact = dims.get(k)
                        if isinstance(fact, dict) and fact.get("value") is not None:
                            dim_values[k] = fact.get("value")

                    record["geometry"] = {
                        "ok": bool(gres.get("ok", False)),
                        "buildings": (1 if has_primary else 0) + extra_buildings,
                        "siteBoundary": has_site,
                        "networks": len(net_values),
                        "networkKinds": [str(x.get("kind")) for x in net_values
                                         if isinstance(x, dict) and x.get("kind")],
                        "unitSystem": gres.get("unit_system") or "unknown",
                        "calibrated": bool(gres.get("calibrated", False)),
                        "dimensions": dim_values,
                        "errors": gres.get("errors") or [],
                        "cached": bool(gres.get("cached", False)),
                    }
                    gs = record["geometry"]
                    if gs["ok"]:
                        bits = [f"{gs['buildings']} building footprint(s)"]
                        if gs["siteBoundary"]:
                            bits.append("site boundary")
                        if gs["networks"]:
                            bits.append(f"{gs['networks']} network/route(s)")
                        bits.append(f"units {gs['unitSystem']}")
                        record["status"] = "geometry processed — " + " · ".join(bits)
                    else:
                        errs = gs["errors"]
                        msg = "; ".join(str(e.get("message") or e.get("code") or e)
                                        for e in errs[:3] if isinstance(e, dict))
                        record["status"] = "geometry parse failed" + (f": {msg}" if msg else "")
                else:
                    record["status"] = "неподдържан за разбиране — записан като доказателство"
                    record["parsedAs"] = "stored-only"
            except Exception as ex:
                record["status"] = f"грешка при разчит: {ex}"
                record["parsedAs"] = "error"
            record["ok"] = True
            if record.get("parsedAs") == "geometry-source":
                gs = record.get("geometry") or {}
                if gs.get("ok"):
                    record["spatialNote"] = (
                        f"геометрията е разчетена СЕГА: {int(gs.get('buildings') or 0)} сграден(и) контур(а)"
                        f"; парцел={'да' if gs.get('siteBoundary') else 'не'}"
                        f"; мрежи/маршрути={int(gs.get('networks') or 0)}. "
                        "Ще бъде слята с PDF/DOCX доказателствата при следващия Cost Estimation run."
                    )
                else:
                    record["spatialNote"] = (
                        "геометричният файл е приет, но НЕ е разчетен успешно; "
                        "пространствена сцена няма да се измисля."
                    )
            else:
                record["spatialNote"] = (
                    "този файл сам по себе си не носи CAD/BIM геометрия; "
                    "скенът показва какво реално се разбра от него"
                )
            record["saved"] = dst.name
            man = inbox / "manifest.json"
            manifest = json.loads(man.read_text(encoding="utf-8-sig")) if man.exists() else {"files": []}
            manifest["files"].append(record)
            man.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
            return self._json(record)
        if u.path == "/api/estimation/run":
            # единичен полет + спиране (B3/B4): припокриване = 409, cancel = cooperative
            with _RUNNING_LOCK:
                if _EST_RUNNING:
                    return self._json({"error": "оценката вече тече — изчакай или я Спри (Cancel)"}, 409)
                _EST_RUNNING = True
                import threading as _th
                _EST_CANCEL = _th.Event()
            try:
                return _estimation_run_body(self)
            finally:
                with _RUNNING_LOCK:
                    _EST_RUNNING = False
                    _EST_CANCEL = None


        if u.path == "/api/estimation/resolve":
            # HUMAN DOOR — същият канал като /api/resolve за тендери, но с ЦЕНА:
            # операторът нарича единична цена за COST_NOT_FOUND ред. Пише се в
            # estimation_prices.json (извън изтриваемия инбокс — преживява clear, A1),
            # в human_resolutions като одиторска следа; по избор влиза във фирмения справочник.
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                return self._json({"error": "bad json"}, 400)
            desc = str(body.get("desc") or "").strip()
            unit = str(body.get("unit") or "бр").strip() or "бр"
            note = str(body.get("note") or "").strip()
            try:
                price = float(body.get("price"))
            except (TypeError, ValueError):
                return self._json({"error": "липсва валидна цена (price)"}, 400)
            if not desc or price <= 0:
                return self._json({"error": "нужни са desc и положителна цена"}, 400)
            (DEMO / "estimation_inbox").mkdir(parents=True, exist_ok=True)
            mp_path = _estimation_prices_path()
            store = {}
            if mp_path.exists():
                try:
                    store = json.loads(mp_path.read_text(encoding="utf-8-sig")) or {}
                except Exception:
                    store = {}
            mkey = desc.lower() + " ∥ " + unit
            store[mkey] = {"price": round(price, 4), "unit": unit, "desc": desc,
                           "note": note, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "actor": "operator"}
            mp_path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
            try:  # одиторска следа — същият ledger като тендерния resolve
                save_resolution(f"est::{mkey}", note or f"ръчна цена {price} €/{unit}", "operator")
            except Exception as ex:
                print(f"est resolution audit fail: {ex}")
            lib_added = 0
            if body.get("saveToLibrary"):
                today = time.strftime("%Y-%m-%d")
                item = {"desc": desc, "unit": unit,
                        "money": {"amount": price, "currency": "EUR", "vatIncluded": False, "asOf": today},
                        "origin": {"kind": "manual_entry", "ref": "estimation resolve",
                                   "note": f"ръчно въведена цена от оператор на {today}"},
                        "source": "manual:estimation", "status": "active"}
                seed = DEMO / f"costdb_seed_manual_{time.strftime('%Y%m%d_%H%M%S')}.json"
                seed.write_text(json.dumps([item], ensure_ascii=False, indent=1), encoding="utf-8")
                try:
                    cdb = _mod("costdb")
                    sync = cdb.sync_json_sources(DEMO, os.environ.get("TENDEROPS_DB_PATH"))
                    lib_added = int(sync.get("added", 0))
                except Exception as ex:
                    return self._json({"error": f"справочникът не прие цената: {ex}"}, 500)
            if telemetry:
                try:
                    telemetry.log_event(
                        "ESTIMATION_MANUAL_PRICE",
                        message=f"Manual price for '{desc}': {price} EUR/{unit}",
                        component="server", actor_type="user",
                        payload={"desc": desc, "unit": unit, "price": price,
                                 "saveToLibrary": bool(body.get("saveToLibrary"))},
                        db_path=os.environ.get("TENDEROPS_DB_PATH"))
                except Exception:
                    pass
            return self._json({"ok": True, "desc": desc, "unit": unit, "price": price,
                               "library": lib_added})
        if u.path == "/api/support":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                body = {}
            try:
                cdb = _mod("costdb")
                con = cdb.connect(os.environ.get("TENDEROPS_DB_PATH"))
                con.execute("INSERT INTO audit_events (at,actor,action,detail) VALUES (?,?,?,?)",
                            (time.strftime("%Y-%m-%dT%H:%M:%SZ"), "web-user", "support",
                             json.dumps(body, ensure_ascii=False)[:600]))
                con.commit(); con.close()
            except Exception as ex:
                return self._json({"error": str(ex)}, 500)
            return self._json({"ok": True})
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "outcome":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            cdb = _mod("costdb")
            cdb.record_outcome(os.environ.get("TENDEROPS_DB_PATH"), int(m[3]),
                               bid_amount=body.get("bid_amount"), submitted_at=body.get("submitted_at"),
                               won=body.get("won"), awarded_amount=body.get("awarded_amount"),
                               actual_cost=body.get("actual_cost"), note=str(body.get("note") or ""))
            return self._json({"ok": True})
        if u.path == "/api/approve":
            # Изрично човешко одобрение за генерация (канон 7→12): отделен запис от гейтовете.
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            tid = int(body.get("tender_id") or 0)
            if not tid:
                return self._json({"error": "missing tender_id"}, 400)
            pack = _load_pack_for(tid)
            if pack is None:
                return self._json({"error": "няма обработен пакет"}, 404)
            cdb = _mod("costdb")
            dbp = os.environ.get("TENDEROPS_DB_PATH")
            res = cdb.get_resolutions(dbp, tid)
            open_gates = [l.get("key") for l in pack.get("boq", [])
                          if (l.get("rule") == "EST" or l.get("flag")) and l.get("key") not in res]
            if open_gates:
                return self._json({"ok": False, "error": f"първо разреши портите — {len(open_gates)} отворени",
                                   "openGates": open_gates}, 409)
            note = str(body.get("note") or "").strip()
            cdb.upsert_resolution(dbp, tid, "__approved__",
                                  note=note or "одобрено за генерация",
                                  actor="operator", rationale="human approval for generation",
                                  evidence={"by": "approve endpoint", "note": note})
            if telemetry:
                try:
                    telemetry.log_event(
                        telemetry.EventType.HUMAN_GATE_RESOLVED,
                        message=f"Human approval for generation recorded for tender {tid}",
                        tender_id=tid, component="server", actor_type="user",
                        payload={"note": note}, db_path=dbp)
                except Exception:
                    pass
            return self._json({"ok": True, "approved": True, "tenderId": tid})
        if u.path == "/api/resolve":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            key = str(body.get("key", "")).strip()
            note = str(body.get("note", "")).strip()
            if not key:
                return self._json({"error": "missing key"}, 400)
            tid = body.get("tender_id")
            rec = save_resolution(key, note, "operator", tender_id=int(tid) if tid else None)
            return self._json({"ok": True, "key": key, "resolution": rec})
        if u.path == "/api/resolve_all":
            # Bulk human gate: resolve EVERY open blocker of one tender with one decision.
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                return self._json({"error": "bad json"}, 400)
            tid = body.get("tender_id")
            if not tid:
                return self._json({"error": "missing tender_id"}, 400)
            tid = int(tid)
            note = str(body.get("note") or "").strip()
            pack = _load_pack_for(tid)
            if pack is None:
                return self._json({"error": "няма обработен пакет — първо „Обработи“"}, 404)
            cdb = _mod("costdb")
            dbp = os.environ.get("TENDEROPS_DB_PATH")
            done = cdb.get_resolutions(dbp, tid)
            open_keys = [l["key"] for l in pack.get("boq", [])
                         if (l.get("rule") == "EST" or l.get("flag")) and l.get("key") not in done]
            with_prices = bool(body.get("withPrices"))
            skipped = []
            for k in open_keys:
                cand = None
                if with_prices:
                    line = next((l for l in pack["boq"] if l.get("key") == k), {})
                    cand = ((line.get("match") or {}).get("top_candidates") or [None])[0]
                    price = _candidate_price(cdb, dbp, (cand or {}).get("id")) if cand else None
                    if price is None:
                        skipped.append(k)  # няма реален кандидат под портата — остава отворена, гласно
                        continue
                ev = {"batch": True, "note": note}
                rnote = note or "групово решение (resolve all)"
                rationale = "bulk resolve-all"
                if with_prices:
                    ev["candidate"] = {**cand, "priceEur": price}
                    rnote = (note + " | " if note else "") + \
                        f"кандидат {cand.get('ref') or cand.get('desc') or ''} · {price} €/{(line or {}).get('unit') or 'бр'} · score {(cand or {}).get('score')}"
                    rationale = "bulk resolve-all (with available candidate prices)"
                cdb.upsert_resolution(dbp, tid, k,
                                      note=rnote,
                                      actor="operator", rationale=rationale,
                                      evidence=ev)
            if telemetry:
                try:
                    telemetry.log_event(
                        telemetry.EventType.HUMAN_GATE_RESOLVED,
                        message=f"Bulk resolve-all: {len(open_keys)} blockers resolved for tender {tid}",
                        tender_id=tid, component="server", actor_type="user",
                        payload={"keys": open_keys, "note": note}, db_path=dbp)
                except Exception:
                    pass
            return self._json({"ok": True, "resolved": len(open_keys) - len(skipped),
                               "keys": [k for k in open_keys if k not in skipped],
                               "skippedNoPrice": skipped, "withPrices": with_prices})
        if len(m) == 5 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "clear":
            # "Изчисти предишния ход": removes the CURRENT instance artifacts so the
            # tender goes back to preparation. Audit surfaces (runs/matches/resolutions)
            # are NEVER touched — history is append-only by canon.
            tid = int(m[3])
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception:
                body = {}
            keep_files = bool(body.get("keepFiles", True))
            with _RUNNING_LOCK:
                busy = tid in _RUNNING
            if busy:
                return self._json({"error": "обработката тече — първо я Спри"}, 409)
            removed = []
            for p in (PROCESSED / f"{tid}.json", PROCESSED / f"{tid}.error.json",
                      PROCESSED / f"submission_{tid}.zip"):
                if p.exists():
                    p.unlink()
                    removed.append(p.name)
            sub_dir = PROCESSED / f"submission_{tid}"
            if sub_dir.exists():
                shutil.rmtree(sub_dir, ignore_errors=True)
                removed.append(sub_dir.name + "/")
            if not keep_files:
                fdir = PROCESSED / "files" / str(tid)
                if fdir.exists():
                    shutil.rmtree(fdir, ignore_errors=True)
                    removed.append(f"files/{tid}/ (кеш на документите)")
            if telemetry:
                try:
                    telemetry.log_event(
                        telemetry.EventType.RUN_CANCELLED,
                        message=f"Run instance cleared for tender {tid}: {removed}",
                        severity=telemetry.Severity.WARNING,
                        tender_id=tid, component="server", actor_type="user",
                        payload={"removed": removed, "keep_files": keep_files},
                        db_path=os.environ.get("TENDEROPS_DB_PATH"))
                except Exception:
                    pass
            return self._json({"ok": True, "tenderId": tid, "removed": removed,
                               "note": "историята (runs/matches/resolutions) се пази — изчистен е само текущият артефакт"})
        if u.path == "/api/agent/note":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            content = str(body.get("content") or "")
            if len(content) > 60000:
                return self._json({"error": "бележката е над 60 KB"}, 413)
            p = DEMO / "agent_note.md"
            # Never let a half-loaded editor clobber the standing memo with emptiness.
            if not content.strip() and not body.get("clear") and p.exists() and p.stat().st_size > 0:
                return self._json({"error": "отказ: празна бележка няма да изтрие съществуващата — изпрати {\"clear\": true} ако наистина чистиш"}, 409)
            p.write_text(content, encoding="utf-8")
            return self._json({"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))})
        if u.path == "/api/agent/chat":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return self._json({"error": "bad json"}, 400)
            msgs = body.get("messages") or []
            if not isinstance(msgs, list) or not msgs:
                return self._json({"error": "messages[] е задължителен"}, 400)
            allow_mut = bool(body.get("allow_mutation"))
            llm_cfg = settings().get("llm") or {}
            if not llm_cfg.get("base_url"):
                return self._json({"error": "LLM не е конфигуриран — попълни base_url/model в LLM хъба"}, 412)
            try:
                res = _mod("ai_tools").agent_chat(llm_cfg, msgs, allow_mutation=allow_mut)
            except Exception as ex:
                return self._json({"error": f"agent: {ex}"}, 500)
            code = 200 if res.get("ok") else 502
            return self._json(res, code)
        if len(m) == 6 and m[1] == "api" and m[2] == "tender" and m[3].isdigit() and m[4] == "spatial" and m[5] == "cancel":
            # Scoped cancel: stops ONLY the spatial build of this tender.
            # Never touches the pipeline process event, the server, or other tenders.
            tid = int(m[3])
            with _RUNNING_LOCK:
                ev = _SPATIAL.get(tid)
            if not ev:
                return self._json({"error": "няма течаща spatial построяване за тази поръчка"}, 409)
            ev.set()
            return self._json({"ok": True, "cancelled": f"spatial:{tid}"})
        return self._json({"error": "not found"}, 404)

def run():
    _ensure_user_files()
    try:
        cdb = _mod("costdb")
        sync = cdb.sync_json_sources(DEMO)
        print(f"costdb sync: +{sync['added']}/skip {sync['skipped']} -> {os.environ['TENDEROPS_DB_PATH']}")
        print(f"costdb stats: {cdb.stats()}")
    except Exception as ex:
        print(f"costdb SYNC FAILED: {ex}")  # гласно; сървърът върви, ценовият слой няма
    try:  # B2: zombie runs from a killed mid-run process would be polled forever — close them
        swept = _mod("costdb").sweep_interrupted_runs(os.environ.get("TENDEROPS_DB_PATH"),
                                                      note="server restart")
        if swept:
            print(f"runs sweep: {swept} interrupted run(s) closed")
    except Exception as ex:
        print(f"runs sweep failed (loud, non-fatal): {ex}")
    # Structural guard: never silently co-bind :8077 with an already-running TenderOps
    # (Windows SO_REUSEADDR would let both bind and split connections between them).
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/tenders", timeout=2) as r:
            if r.status == 200:
                try:
                    print(f"TenderOps вече върви на :{PORT} — вторият екземпляр отказва. Затвори стария първо.")
                except Exception:
                    pass
                return
    except urllib.error.URLError:
        pass  # никой не слуша -> продължаваме
    try:
        print(f"TenderOps demo -> http://{HOST}:{PORT}")
    except Exception:
        pass
    # Daily content snapshot (owner decision 2026-09-06): daemon thread, idempotent per day.
    try:
        import threading as _th

        def _snap_loop():
            while True:
                try:
                    res = _mod("maintenance").maybe_daily_snapshot()
                    if res:
                        print(f"daily snapshot: {res['day']} · {res['files']} файла · {res['bytes'] // 1048576} MB")
                except Exception as ex:
                    print(f"daily snapshot fail (loud, non-fatal): {ex}")
                _th.Event().wait(3600)

        _th.Thread(target=_snap_loop, daemon=True, name="daily-snapshot").start()
    except Exception:
        pass
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()


def _estimation_run_body(self):
    """PRIVATE DOOR (канон §3B): инбокс-evidence -> costdb цени -> пакет + остойностена КСС.
    Never-empty owner rule; cancel checks between files and rows (_EST_CANCEL)."""
    # PRIVATE DOOR (canon §3B): evidence rows from the inbox -> OUR corpus prices ->
    # estimation pack + остойностена КСС. Never-empty applies here too (owner rule).
    inbox = DEMO / "estimation_inbox"
    if not inbox.exists():
        return self._json({"error": "няма качени файлове — първо качи документи"}, 404)
    pipe = _mod("pipeline")
    cdb = _mod("costdb")
    dbp = os.environ.get("TENDEROPS_DB_PATH")
    cdb.sync_json_sources(DEMO, dbp)
    env_id = cdb.ensure_env_version(dbp, note="estimation run")
    raw_rows, used_files, skipped, per_file = [], [], [], {}
    consumed_files = []
    geometry_files = []   # CAD/BIM: геометрично доказателство за spatial v2, не КСС редове
    seen_hashes = set()
    import hashlib as _hl
    for f in sorted(p for p in inbox.glob("*") if p.is_file() and p.suffix.lower() in (".xls", ".xlsx", ".docx", ".pdf", ".dxf", ".ifc")):
        if _EST_CANCEL is not None and _EST_CANCEL.is_set():
            return self._json({"ok": False, "cancelled": True,
                               "error": "оценката е спряна от оператора — нищо не е записано"})
        if _is_estimation_artifact(f.name):
            skipped.append({"file": f.name, "why": "собствен артефакт на TenderOps — генериран изход/пакет, не е ново доказателство"})
            continue
        consumed_files.append(f)  # batch scope: всяка не-артефактна качилка, прочетена от този run
        digest = _hl.sha256(f.read_bytes()).hexdigest()
        if digest in seen_hashes:
            skipped.append({"file": f.name, "why": "точно копие на файл, вече отчетен в този run"})
            continue
        seen_hashes.add(digest)
        if f.suffix.lower() in (".dxf", ".ifc"):
            geometry_files.append(f)
            continue  # чертег/BIM не носи КСС редове — отива в геометричната фаза по-долу
        try:
            if f.suffix.lower() in (".xls", ".xlsx"):
                got = pipe.parse_kss_priced(f)  # respects the document's own prices (OBSERVED)
                if not got:
                    rows_ps = pipe.parse_price_sheet(f)
                    got = [{"desc": r["desc"], "unit": r.get("unit") or "бр",
                            "qty": float(r.get("qty") or 1.0), "section": "вход",
                            "code": "", "own_price": float(r["price"])}
                           for r in rows_ps if r.get("price")]
            elif f.suffix.lower() == ".docx":
                got = pipe.parse_kss_docx(f)
            else:
                got = pipe.parse_kss_pdf(f)
        except Exception:
            continue
        if got:
            used_files.append(f.name)
            raw_rows.extend(got)
            per_file[f.name] = got
    # evidence merge: (desc, unit, qty) defines the scope row; a priced variant
    # always wins over its unpriced twin (signed/template duality). Conflicting
    # own prices for the same scope stay visible as a note (canon: loud, never silent).
    by_scope = {}
    order = []
    for r in raw_rows:
        k = ((r.get("desc") or "").strip().lower(), (r.get("unit") or "").strip(),
             round(float(r.get("qty") or 0), 3), (r.get("sub") or "").strip().lower())
        if k not in by_scope:
            by_scope[k] = r
            order.append(k)
            continue
        w = by_scope[k]
        if not w.get("own_price") and r.get("own_price"):
            w_origin = w.get("_src_note", "")
            r["_src_note"] = w_origin
            by_scope[k] = r
        elif w.get("own_price") and r.get("own_price") and abs(float(r["own_price"]) - float(w["own_price"])) > 0.005:
            w["_price_conflict"] = w.get("_price_conflict", []) + [r["own_price"]]
    raw_rows = [by_scope[k] for k in order]
    if not raw_rows:
        return self._json({"error": "от качените файлове не са извлечени количествени редове — няма какво да се остойности (никога празен резултат)"}, 422)
    # HUMAN DOOR (канон §3B): цени, въведени от оператора през /api/estimation/resolve,
    # живеят в estimation_prices.json (извън инбокса) и бият пред costdb — човекът е по-авторитетен от справочника.
    manual_prices = {}
    mp_path = _estimation_prices_path()
    if mp_path.exists():
        try:
            manual_prices = json.loads(mp_path.read_text(encoding="utf-8-sig")) or {}
        except Exception:
            manual_prices = {}
    boq, n_priced = [], 0
    for i, r in enumerate(raw_rows):
        if i % 16 == 0 and _EST_CANCEL is not None and _EST_CANCEL.is_set():
            return self._json({"ok": False, "cancelled": True,
                               "error": "оценката е спряна от оператора — нищо не е записано"})
        line = {"key": f"R{i+1}", "row": i + 1, "no": f"{i+1}.0",
                "section": r.get("section") or "вход", "sub": r.get("sub") or "",
                "desc": r["desc"], "unit": r.get("unit") or "бр",
                "qty": round(float(r.get("qty") or 0), 3), "code": r.get("code") or "",
                "tone": "web", "flag": "", "note": ""}
        if r.get("own_price"):
            net = float(r["own_price"])
            note_txt = "собствена цена от качения файл (OBSERVED)"
            if r.get("_price_conflict"):
                note_txt += f" | КОНФЛИКТ: друг файл дава {', '.join(str(x) for x in r['_price_conflict'])} € за същия ред"
            line.update({"rule": "CSV", "ruleLabel": "Собствена цена (качен файл)",
                         "unitEur": round(net, 4), "sumEur": round(net * line["qty"], 2),
                         "matUnitEur": round(net, 4), "laborHoursUnit": 0, "laborEur": 0, "equipmentEur": 0,
                         "match": {"method": "user_file", "confidence": "high"},
                         "source": {"origin": "user_upload", "evidenceState": "OBSERVED"},
                         "note": note_txt})
            n_priced += 1
        else:
            mp = manual_prices.get((r.get("desc") or "").strip().lower() + " ∥ " + (r.get("unit") or "бр").strip())
            if mp is not None:
                net = float(mp["price"])
                note_txt = f"РЪЧНА цена от човек (OBSERVED) · {mp.get('at', '')}"
                if mp.get("note"):
                    note_txt += f" | {mp['note']}"
                line.update({"rule": "HUMAN", "ruleLabel": "Ръчна цена (човек)",
                             "unitEur": round(net, 4), "sumEur": round(net * line["qty"], 2),
                             "matUnitEur": round(net, 4), "laborHoursUnit": 0, "laborEur": 0, "equipmentEur": 0,
                             "match": {"method": "manual_entry", "confidence": "high"},
                             "source": {"origin": "manual_entry", "evidenceState": "OBSERVED"},
                             "note": note_txt})
                n_priced += 1
                boq.append(line)
                continue
            best, score, evidence = pipe.match_cost_v2(r, dbp)
            if best:
                net = best["unitEur"] / 1.2 if best["vatIncluded"] else best["unitEur"]
                line.update({"rule": "CSV", "ruleLabel": "Локален разход (costdb)",
                             "unitEur": round(net, 4), "sumEur": round(net * line["qty"], 2),
                             "matUnitEur": round(float((best.get("components") or {}).get("materialsEur") or net), 4),
                             "laborHoursUnit": 0,
                             "laborEur": round(float((best.get("components") or {}).get("laborEur") or 0), 4),
                             "equipmentEur": round(float((best.get("components") or {}).get("equipmentEur") or 0), 4),
                             "match": evidence,
                             "source": {"id": best.get("id"), "sourceKey": best.get("sourceKey"),
                                        "ref": best.get("ref"), "asOf": best.get("asOf"),
                                        "status": best.get("status"), "evidenceState": "OBSERVED"},
                             "note": f"costdb ← {best['ref']} · {evidence['method']} · score {score:.1f}"})
                n_priced += 1
            else:
                line.update({"rule": "EST", "ruleLabel": "HUMAN_INPUT_REQUIRED", "tone": "est",
                             "unitEur": 0, "sumEur": 0, "matUnitEur": 0, "laborHoursUnit": 0,
                             "laborEur": 0, "flag": "COST_NOT_FOUND", "match": evidence,
                             "note": "Няма надежден локален match — цената НЕ е измислена; изисква човек."})
        boq.append(line)
    total = round(sum(l["sumEur"] for l in boq), 2)
    vat = round(total * 0.2, 2)
    sections = {}
    for l in boq:
        sections[l["section"]] = round(sections.get(l["section"], 0) + l["sumEur"], 2)
    est_id = time.strftime("EST%Y%m%d_%H%M%S")
    if (inbox / f"estimation_{est_id}.json").exists():  # B3: same-second rerun must never clobber
        for _sfx in "abcdefghij":
            if not (inbox / f"estimation_{est_id}{_sfx}.json").exists():
                est_id += _sfx
                break
    pricing = {"tenderId": est_id, "currency": "EUR", "vatRate": 0.2,
               "totalExclVat": total, "vat": vat, "totalInclVat": round(total + vat, 2),
               "sectionTotals": sections, "costEnvVersion": env_id,
               "costVersion": f"SQLite DB v{cdb.DB_VERSION} · env#{env_id} · canonical local cost environment"}
    # scope template: the buyer's own unpriced BoQ sheet defines the output layout.
    # Priced uploads are price evidence, not templates. Deterministic choice:
    # NEWEST unpriced upload wins (the user just dropped the current project's
    # template); row count breaks same-second ties only.
    price_index = {((l.get("desc") or "").strip().lower(), (l.get("unit") or "").strip(),
                    round(float(l.get("qty") or 0), 3), (l.get("sub") or "").strip().lower()): l
                   for l in boq}
    tpl_name, tpl_rows = None, None
    for fname, rows in per_file.items():
        if Path(fname).suffix.lower() not in (".xls", ".xlsx") or len(rows) < 3:
            continue
        if any(r.get("own_price") for r in rows):
            continue
        if tpl_name is None or fname > tpl_name or (fname == tpl_name and len(rows) > len(tpl_rows)):
            tpl_name, tpl_rows = fname, rows
    scope = None
    if tpl_name:
        n_p, n_h, n_u, scope_sum = 0, 0, 0, 0.0
        for r in tpl_rows:
            k = ((r.get("desc") or "").strip().lower(), (r.get("unit") or "").strip(),
                 round(float(r.get("qty") or 0), 3), (r.get("sub") or "").strip().lower())
            line = price_index.get(k)
            if line is None:
                n_u += 1
            elif line.get("rule") == "EST":
                n_h += 1
            else:
                n_p += 1
                scope_sum += float(line.get("sumEur") or 0)
        scope = {"template": tpl_name, "rows": len(tpl_rows),
                 "priced": n_p, "awaitingHuman": n_h, "unmatched": n_u,
                 "totalExclVat": round(scope_sum, 2), "vat": round(scope_sum * 0.2, 2),
                 "totalInclVat": round(scope_sum * 1.2, 2)}
    pack = {"tenderId": est_id, "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "estimation — частен проект (канон §3B)",
            "boq": boq, "pricing": pricing,
            "documents": [{"name": n, "origin": "estimation inbox (user upload)"} for n in used_files],
            "skippedEvidence": skipped,
            "scopeTemplate": tpl_name, "scope": scope,
            "pipe": {"evidence": f"{len(used_files)} файла", "costdb": f"{cdb.stats(dbp)['active']} активни"},
            "llm": {"ok": False, "text": "", "error": "не е стартиран"}}
    # Spatial v2 geometry evidence (същата фаза като tender pipeline): CAD/BIM + PDF/DOCX
    # от тази партида -> cached extraction -> authority fusion. Loud, never fatal.
    geom_candidates = geometry_files + sorted(
        (f for f in consumed_files if f.suffix.lower() in (".pdf", ".docx")),
        key=lambda p: p.name)
    if geom_candidates:
        try:
            # Cost Estimation owns Spatial. Build geometry evidence directly from
            # THIS estimation batch instead of depending on a pipeline helper that
            # may not exist in all builds.
            _geom = _mod("spatial_geometry")
            _fusion = _mod("spatial_fusion")
            _extracts = _geom.cached_extract(geom_candidates, DEMO.parent / "geometry_cache")
            _gev = _fusion.fuse(_extracts)
            if _gev:
                pack["geometry_evidence"] = _gev
        except Exception as gex:
            print(f"[server] estimation geometry stage failed (loud, non-fatal): {gex}")
    pack_path = inbox / f"estimation_{est_id}.json"
    pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
    xlsx_name = f"KSS_ocenena_{est_id}.xlsx"
    try:
        sub = _mod("submission")
        if tpl_name:
            sub._write_kss_template(inbox / xlsx_name, pack, inbox / tpl_name, price_index)
        else:
            sub._write_kss(inbox / xlsx_name, pack)
    except Exception as ex:
        xlsx_name = None
        _log_err = str(ex)
    # Batch scope (2026-09-09): след успешен run изконсумираните качилки излизат от
    # корена на инбокса в runs/<est_id>/ — следващ run вижда САМО новата партида,
    # старите стават инертен архив; пропаднал run (или ненаписана КСС) не архивира нищо.
    if xlsx_name:
        try:
            runs_dir = inbox / "runs" / est_id
            for f in consumed_files:
                if not f.exists():
                    continue
                runs_dir.mkdir(parents=True, exist_ok=True)
                try:
                    f.rename(runs_dir / f.name)
                except OSError:
                    shutil.move(str(f), str(runs_dir / f.name))
            man = inbox / "manifest.json"
            manifest = json.loads(man.read_text(encoding="utf-8-sig")) if man.exists() else {"files": []}
            names = {p.name for p in inbox.glob("*") if p.is_file()}
            manifest["files"] = [r for r in manifest.get("files") or [] if r.get("saved") in names]
            man.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as ex:
            print(f"[server] batch archive failed (loud, non-fatal): {ex}")
    if telemetry:
        try:
            telemetry.log_event(
                "ESTIMATION_RUN",
                message=f"Estimation run {est_id}: {len(boq)} rows, {n_priced} priced, {total:.2f} EUR",
                component="server", actor_type="user",
                payload={"files": used_files, "rows": len(boq), "priced": n_priced,
                         "skipped": [s["file"] for s in skipped]},
                db_path=dbp)
        except Exception:
            pass
    gaps = [{"key": l["key"], "row": l["row"], "desc": l["desc"],
             "unit": l["unit"], "qty": l["qty"],
             "match": {"top_candidates": ((l.get("match") or {}).get("top_candidates") or [])[:3],
                       "method": (l.get("match") or {}).get("method")}}
            for l in boq if l.get("rule") == "EST"]
    return self._json({"ok": True, "estimationId": est_id, "rows": len(boq),
                       "priced": n_priced, "awaitingHuman": len(boq) - n_priced,
                       "totalExclVat": total, "totalInclVat": pricing["totalInclVat"],
                       "costEnvVersion": env_id, "files": used_files, "skipped": skipped,
                       "scope": scope, "gaps": gaps,
                       "kssXlsx": xlsx_name, "packJson": pack_path.name,
                       "download": f"/api/estimation/download?file={xlsx_name}" if xlsx_name else None})


if __name__ == "__main__":
    # ENTRYPOINT LAST (regression 2026-09-09): run() blocks in serve_forever, so any
    # top-level def below this block never binds -> NameError at request time.
    run()
