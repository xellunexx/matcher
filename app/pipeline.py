# -*- coding: utf-8 -*-
"""Per-tender processing pipeline.

Registry record (ЦАИС ЕОП) -> download docs via signed urls -> parse КСС spreadsheets
-> deterministic pricing against the LOCAL cost DB only -> optional LLM advisory pass
-> processed pack in the same JSON shape the UI already renders for 601701.

Rules: LLM reasons and proposes; deterministic tooling disposes.
No fabricated numbers — a row without a local cost becomes EST + COST_NOT_FOUND.
"""
import json, random, re, sys, time, urllib.error, urllib.request
from pathlib import Path

try:  # package context
    from . import eop, llm, costdb, telemetry, matcher
    from . import validate as _validate
except Exception:  # loaded standalone via importlib (frozen exe / dev script)
    import importlib.util as _ilu
    def _sib(name):
        spec = _ilu.spec_from_file_location(name, str(Path(__file__).resolve().parent / f"{name}.py"))
        m = _ilu.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
        return m
    eop = _sib("eop"); llm = _sib("llm"); costdb = _sib("costdb"); _validate = _sib("validate")
    telemetry = _sib("telemetry"); matcher = _sib("matcher")

EURBGN = 1.95583
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_ROWS = 20000

UA = {"User-Agent": "TenderOps/0.1 (public procurement research)"}

_UNIT_MAP = {"м": "m", "м.": "m", "м1": "m1", "м2": "m2", "кв.м": "m2", "кв.м.": "m2",
             "м3": "m3", "куб.м": "m3", "куб.м.": "m3", "бр.": "бр", "компл.": "компл",
             "кг.": "кг", "т.": "т", "л.м.": "л.м", "ч.": "ч", "час": "ч", "час.": "ч"}
_STOP = {"на", "за", "от", "с", "по", "в", "до", "при", "вкл", "включително", "доставка",
         "монтаж", "изпълнение", "материали", "работа", "без", "със", "над", "под", "вид",
         "нов", "нова", "нови", "ново", "съществуваща", "съществуващ", "съществуващи"}
_HDR_HINTS = ("наименование", "общо", "обща сума", "сумарно", "ддс", "total", "ед. цена", "мярка", "количеств")
_HDR_PREFIX = ("общо", "обща сума", "сумарно", "наименование", "ед. цена")


_HDR_WORDS = ("общо", "обща сума", "сумарно", "наименование", "количеств", "мярка")


def _is_header_low(low):
    """Header/total noise, not content. Fires on prefix forms or short hint-rows only;
    a normal spec line like „Проверка на ДДС нормализация…“ must survive (golden 08).
    „ддс“ as a token no longer counts as header evidence (too false-positive)."""
    if low.startswith(_HDR_PREFIX):
        return True
    words = set(re.findall(r"[а-яa-z0-9.]+", low))
    return len(low) <= 40 and any(h in low for h in _HDR_WORDS) and len(words) <= 3
_KSS_HINTS = ("ксс", "количеств", "сметк", "boq", "специфик")


def _log(msg):
    try:
        print(f"[pipeline] {msg}")
    except Exception:
        pass


class CancelledError(Exception):
    """Raised cooperatively when the operator stops a running process."""


_cancel_cb = None

def _check_cancel():
    if _cancel_cb and _cancel_cb():
        raise CancelledError("спряна от потребителя")


# ---------------- transport ----------------
def _open_with_retry(req, tries=4, base_delay=0.4, timeout=120):
    """Bounded retry + exponential backoff + jitter for document GETs.
    Retryable: URLError / timeouts / 5xx / 429. Terminal: other 4xx (fail fast)."""
    last = None
    for attempt in range(1, tries + 1):
        _check_cancel()
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        if attempt < tries:
            time.sleep(min(8.0, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 0.25))
    raise last


# ---------------- download ----------------
def _guess_role(name, ext):
    n = name.lower()
    if ext in (".xlsx", ".xls") or any(h in n for h in _KSS_HINTS):
        return "КСС/количествена сметка"
    if "техничес" in n:
        return "техническа спецификация"
    if "документаци" in n:
        return "документация"
    if "договор" in n:
        return "проектодоговор"
    return "приложение"


def download_docs(rec, files_dir):
    """Download all registry documents via signed urls. Returns per-doc status list."""
    files_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for d in rec.get("documents") or []:
        _check_cancel()
        entry = {"name": d.get("name"), "docId": d.get("docId"), "kind": "регистър",
                 "fmt": (d.get("ext") or "?"), "role": _guess_role(d.get("name") or "", d.get("ext") or ""),
                 "origin": "ЦАИС ЕОП (публично ниво)", "state": "само инвентар"}
        # First-class local channel: ЦАИС „Експорт" files attached by the operator
        # (many tenders expose the КСС matrix only inside the export bundle).
        lp = d.get("localPath")
        if lp and Path(lp).exists():
            entry["origin"] = "локален ЦАИС експорт (подаден от оператора)"
            entry["state"] = f"локален файл ({Path(lp).stat().st_size // 1024} KB)"
            entry["localPath"] = str(lp)
            out.append(entry); continue
        ext = (d.get("ext") or "").lower()
        if ext not in (".xlsx", ".xls", ".pdf", ".doc", ".docx", ".zip", ".rar", ".dxf", ".ifc"):
            entry["state"] = "пропуснат (неподдържан формат)"
            out.append(entry); continue
        try:
            sig = eop.signed_url(d["docId"])
            url = sig.get("Url") if isinstance(sig, dict) else sig
            if not url:
                raise ValueError("няма signed url")
            size = int(d.get("size") or 0)
            if size and size > MAX_FILE_BYTES:
                raise ValueError(f"файлът е {size // 1048576} MB > лимит {MAX_FILE_BYTES // 1048576} MB")
            safe = re.sub(r'[\\/:*?"<>|]+', "_", d.get("name") or f"doc_{d['docId']}")[:120]
            dst = files_dir / f"{d['docId']}_{safe}"
            if not dst.exists():
                # heal legacy local-export entries: any cached file with this docId wins
                hit = next(iter(sorted(files_dir.glob(f"{d['docId']}_*"))), None)
                if hit and hit.stat().st_size > 0:
                    dst = hit
            if dst.exists() and dst.stat().st_size > 0:
                entry["state"] = f"кеш ({dst.stat().st_size // 1024} KB)"
                entry["localPath"] = str(dst)
                out.append(entry)
                continue
            req = urllib.request.Request(url, headers=UA)
            written = 0
            with _open_with_retry(req, timeout=120) as r, open(dst, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_FILE_BYTES:
                        raise ValueError("над лимита при дърпане")
                    f.write(chunk)
            entry["state"] = f"изтеглен ({written // 1024} KB)"
            entry["localPath"] = str(dst)
        except Exception as ex:
            entry["state"] = f"грешка при дърпане: {ex}"
        out.append(entry)
    return out


# ---------------- КСС parsing ----------------
def _scoped_sheet_rows(path):
    """Rows restricted to the Обобщена (summary) sheet when the workbook has one.
    Detail sheets (Подробна КС, част ЕЛ/ВиК/ОВК…) break down the SAME work the
    summary aggregates — pricing both views double-counts quantities."""
    rows = list(_iter_sheet_rows(path))
    summary = [r for r in rows if "обобщ" in str(r[0] or "").lower()]
    return summary if summary else rows


def _iter_sheet_rows(path):
    """Yield (sheet_name, row_values) for xlsx (openpyxl) or xls (xlrd)."""
    suf = path.suffix.lower()
    if suf == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    yield ws.title, row
        finally:
            wb.close()
    elif suf == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        for sh in wb.sheets():
            for i in range(sh.nrows):
                yield sh.name, sh.row_values(i)


def _col_roles(vals):
    """Map КСС header cells to column indices: № | Описание | Ед. мярка | К-во."""
    roles = {}
    for i, v in enumerate(vals):
        s = str(v).strip().lower() if v is not None else ""
        if s in ("№", "no", "п/п", "n"):
            roles.setdefault("no", i)
        elif "описание" in s or "наименование" in s:
            roles.setdefault("desc", i)
        elif "мярка" in s or s in ("ед.", "ед", "м.е.", "м. е.", "е.м.", "ед.м.", "ед. м.", "едм", "единица"):
            roles.setdefault("unit", i)
        elif "шифър" in s or "код" in s or "позиция" in s:
            roles.setdefault("code", i)
        elif "к-во" in s or "колич" in s or "кол-во" in s or s == "кол.":
            roles.setdefault("qty", i)
    return roles if ("desc" in roles or "qty" in roles) else None


def _cell(vals, i):
    return vals[i] if i is not None and i < len(vals) else None


def _plausible_qty(n):
    """Количествата в КСС са винаги положителни; отрицателни/абсурдни/години — не."""
    return isinstance(n, (int, float)) and not isinstance(n, bool) and 0 < n < 1e8 and not (1900 <= n <= 2100)


def pick_kss_paths(doc_statuses):
    """Candidates, КСС-looking names first: spreadsheets (xlsx/xls), then docx tables,
    then PDFs (количествени сметки embedded in specs/documentation)."""
    got = [(d.get("name") or "", Path(d["localPath"])) for d in doc_statuses if d.get("localPath")]
    def hot_first(lst):
        hot = [(n, p) for n, p in lst if any(h in n.lower() for h in _KSS_HINTS)]
        return hot + [(n, p) for n, p in lst if (n, p) not in hot]
    sheets = [(n, p) for n, p in got if p.suffix.lower() in (".xlsx", ".xls")]
    docx = [(n, p) for n, p in got if p.suffix.lower() == ".docx"]
    pdfs = [(n, p) for n, p in got if p.suffix.lower() == ".pdf"]
    return hot_first(sheets) + hot_first(docx)[:1] + hot_first(pdfs)[:4]


_GEOMETRY_EXTS = (".dxf", ".ifc", ".pdf", ".docx")


def pick_geometry_paths(doc_statuses):
    """Geometry-evidence candidates (spatial v2, contract §3): DXF/IFC drawings first
    (highest authority tiers), then PDF/DOCX documents that may carry dimensions.
    Only files that actually landed on disk under files/<tid>/ are returned.
    No candidates -> the geometry stage is skipped entirely (no pack key added)."""
    got = [(d.get("name") or "", Path(d["localPath"])) for d in doc_statuses or [] if d.get("localPath")]

    def _of(*exts):
        return [(n, p) for n, p in got if p.suffix.lower() in exts and p.exists()]

    return _of(".dxf", ".ifc") + _of(".pdf", ".docx")


def _geometry_modules():
    """Best-effort import of the parallel spatial-v2 modules, guarded the
    third-party-lib way (any ImportError/Exception -> None). Missing module files
    or missing geometry libs (ezdxf/ifcopenshell/pymupdf) silently-with-log degrade
    the geometry stage to legacy behavior; TenderOps must run with zero of them."""
    try:  # package context (also picks up sys.modules stubs in tests)
        from . import spatial_geometry as sg, spatial_fusion as sf
        return sg, sf
    except Exception:
        pass
    try:  # standalone importlib context (frozen exe / dev scripts)
        stub_sg, stub_sf = sys.modules.get("spatial_geometry"), sys.modules.get("spatial_fusion")
        if stub_sg is not None and stub_sf is not None:
            return stub_sg, stub_sf  # test stubs
        import importlib.util as _ilu
        out = []
        for name in ("spatial_geometry", "spatial_fusion"):
            p = Path(__file__).resolve().parent / f"{name}.py"
            if not p.exists():
                return None, None
            spec = _ilu.spec_from_file_location(name, str(p))
            m = _ilu.module_from_spec(spec)
            sys.modules[name] = m
            spec.loader.exec_module(m)
            out.append(m)
        return out[0], out[1]
    except Exception:
        return None, None


def fuse_geometry_files(paths, cache_dir):
    """Geometry stage (contract §3 -> §4): cached_extract -> authority fusion.
    Returns the fused SpatialGeometryEvidence dict, or None when there are no
    candidates / the modules or third-party libs are absent / anything failed.
    NEVER raises: a bad drawing file must never fail a tender or estimation run."""
    paths = [str(p) for p in paths or []]
    if not paths:
        return None
    sg, sf = _geometry_modules()
    if sg is None or sf is None:
        _log("geometry stage skipped: spatial_geometry/spatial_fusion unavailable")
        return None
    try:
        results = sg.cached_extract(paths, str(cache_dir))
    except Exception as ex:
        _log(f"geometry cached_extract failed (loud, non-fatal): {ex}")
        return None
    try:
        ev = sf.fuse(results or [])
    except Exception as ex:
        _log(f"geometry fuse failed (loud, non-fatal): {ex}")
        return None
    return ev if isinstance(ev, dict) and ev else None


def _bg_num(s):
    """'1 234,56' / '1.234,56' / '1234.56' -> 1234.56"""
    if not isinstance(s, str):
        return None
    t = s.strip().replace("\xa0", "").replace(" ", "")
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def _iter_docx_tables(path):
    """Yield (label, cell_texts) per table row of a .docx (stdlib zip+xml)."""
    import zipfile
    from xml.etree import ElementTree as ET
    NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    for ti, tbl in enumerate(root.iter(NS + "tbl")):
        for tr in tbl.iter(NS + "tr"):
            cells = [" ".join(t.text or "" for t in tc.iter(NS + "t")).strip() for tc in tr.iter(NS + "tc")]
            yield f"таблица {ti + 1}", cells


def _consume(row_iter, max_rows):
    """Shared row logic for spreadsheets and docx tables.
    row_iter yields (section_label, list_of_cell_values)."""
    rows = []
    cur_sec = None
    roles, sub = None, ""
    for sec, vals in row_iter:
        if sec != cur_sec:
            cur_sec, roles, sub = sec, None, ""
        vals = list(vals)
        texts, nums, nums_pos, units = [], [], [], []
        for ci, v in enumerate(vals):
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    continue
                n = _bg_num(s)
                if n is not None and len(s) < 20 and not _norm_unit(s):
                    nums.append(n); nums_pos.append(ci)
                    continue
                if _norm_unit(s) and len(s) <= 12:
                    units.append(s)
                elif len(s) >= 12:
                    texts.append(s)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                nums.append(float(v)); nums_pos.append(ci)
        hdr = _col_roles(vals)
        if hdr and hdr.get("desc") is not None and hdr.get("qty") is not None:
            roles = hdr
            continue
        if roles:
            desc = _cell(vals, roles.get("desc"))
            qty = _cell(vals, roles.get("qty"))
            unit = _cell(vals, roles.get("unit"))
            code = _cell(vals, roles.get("code"))
            qty = _bg_num(qty) if isinstance(qty, str) else qty
            desc = desc.strip() if isinstance(desc, str) else None
            unit = unit.strip() if isinstance(unit, str) else None
            if desc and len(desc) < 10:
                continue  # микро-шум (<10 символа); артикулни имена като „Батут СГ-46“ оцеляват (adversarial 15)
            if not _plausible_qty(qty):
                cand = desc or (texts[0] if len(texts) == 1 else None)
                low = (cand or "").lower()
                if cand and not _is_header_low(low) and len(cand) <= 200:
                    sub = cand[:60]
                continue
            if not desc:
                continue
            low = desc.lower()
            if len(desc) > 500 or _is_header_low(low):
                continue
            rows.append({"section": sec[:60], "sub": sub, "desc": desc,
                         "unit": unit or "бр", "qty": float(qty), "code": (str(code).strip() if code is not None else "")})
        else:
            if not texts or not nums:
                continue
            desc = max(texts, key=len)
            low = desc.lower()
            if len(desc) > 500 or _is_header_low(low):
                continue
            if re.match(r"^\d{2}\.\d{2}\.\d{4}", desc):
                continue
            # №-колоната не е количество: изключваме я когато е малък int в първата значима клетка
            first_pos = nums_pos[0] if nums_pos else None
            qty_cands = []
            for n, posi in zip(reversed(nums), reversed(nums_pos)):
                if first_pos is not None and posi == first_pos and float(n).is_integer() and 0 < n < 10000 \
                        and any(isinstance(v, str) and len(v.strip()) >= 12 for v in vals[:posi] + vals[posi + 1:]):
                    continue
                qty_cands.append(n)
            qty = next((n for n in qty_cands if _plausible_qty(n)), None)
            if qty is None:
                continue
            rows.append({"section": sec[:60], "sub": sub, "desc": desc,
                         "unit": units[0] if units else "бр", "qty": qty, "code": ""})
        if len(rows) >= max_rows:
            print(f"[pipeline] TRUNCATED at {max_rows} parsed rows — останалите редове не влизат в пакета")
            return rows
    return rows


def parse_kss(path, max_rows=MAX_ROWS):
    return _consume(_scoped_sheet_rows(path), max_rows)


# ---------------- price-aware КСС parse (private door / own-price uploads) ----------------
def _price_roles(vals):
    """Normalized header mapping incl. price columns. Handles split headers
    („Коли-чество", „Ед. мярка"), bare „СМР" as the description column, and
    Ед.цена/Стойност columns -> 'price'/'total'. Cell text is normalized
    (spaces/hyphens/dots removed) before matching."""
    roles = {}
    for i, v in enumerate(vals):
        s = str(v).strip().lower() if v is not None else ""
        s2 = re.sub(r"[\s\-.]+", "", s)
        if not s2:
            continue
        if s2 in ("№", "no", "пп", "n"):
            roles.setdefault("no", i)
        elif any(k in s2 for k in ("стойност", "общо", "сума")) and "ед" not in s2 and "колич" not in s2:
            roles.setdefault("total", i)
        elif "цен" in s2 and ("ед" in s2 or "единичн" in s2):
            roles.setdefault("price", i)
        elif any(k in s2 for k in ("описание", "наименование")) or s2 in ("смр", "работи", "дейности"):
            roles.setdefault("desc", i)
        elif "мярка" in s2 or s2 in ("ед", "ме", "ем", "едм", "единица"):
            roles.setdefault("unit", i)
        elif "шифър" in s2 or "код" in s2 or "позиция" in s2:
            roles.setdefault("code", i)
        elif any(k in s2 for k in ("количество", "кво", "колво", "колич")) or s2 == "кол":
            roles.setdefault("qty", i)
    # A header row labels at least two columns; a lone hit is a document/section
    # title (e.g. „ОБОБЩЕНА КОЛИЧЕСТВЕНА СМЕТКА“ -> {qty:0}), never a header.
    return roles if len(roles) >= 2 else None


def _row_ccy(txt):
    low = (txt or "").lower()
    return "BGN" if ("лв" in low or "лева" in low or "bgn" in low) else "EUR"


def parse_kss_priced(path, max_rows=MAX_ROWS):
    """parse_kss variant that KEEPS price columns. Rows gain:
      own_price (EUR, без ДДС, per unit), own_total, own_currency ('лв' detection in headers/labels), price_kind.
    Documents' own prices are OBSERVED evidence (owner canon: submitted docs are authoritative).
    Only header-mapped tables get prices; headerless heuristics cannot place a price column safely."""
    rows = []
    cur_sec, roles, sub, ccy = None, None, "", "EUR"
    for sec, vals in _scoped_sheet_rows(path):
        if sec != cur_sec:
            cur_sec, roles, sub = sec, None, ""
        vals = list(vals)
        flat = " ".join(str(v) for v in vals if v is not None)
        ccy_hit = _row_ccy(flat)
        if ccy_hit == "BGN" and ccy == "EUR" and any(t in flat.lower() for t in ("лв", "лева")):
            ccy = "BGN"
        hdr = _price_roles(vals)
        if hdr and hdr.get("qty") is not None:
            roles = hdr
            continue
        if not roles:
            continue  # headerless: no safe price placement (same boundary as _consume)
        desc = _cell(vals, roles.get("desc"))
        if not isinstance(desc, str) or len(desc.strip()) < 12:
            # desc column absent from the header (e.g. bare „СМР") — longest real text wins
            cands = [str(v).strip() for v in vals if isinstance(v, str) and len(str(v).strip()) >= 12]
            desc = max(cands, key=len) if cands else desc
        qty = _cell(vals, roles.get("qty"))
        unit = _cell(vals, roles.get("unit"))
        code = _cell(vals, roles.get("code"))
        qty = _bg_num(qty) if isinstance(qty, str) else qty
        price = _cell(vals, roles.get("price"))
        total = _cell(vals, roles.get("total"))
        price = _bg_num(price) if isinstance(price, str) else (float(price) if isinstance(price, (int, float)) else None)
        total = _bg_num(total) if isinstance(total, str) else (float(total) if isinstance(total, (int, float)) else None)
        desc = desc.strip() if isinstance(desc, str) else None
        unit = unit.strip() if isinstance(unit, str) else None
        if not _plausible_qty(qty):
            cand = desc
            low = (cand or "").lower()
            if cand and not _is_header_low(low) and len(cand) <= 200:
                sub = cand[:60]
            continue
        if not desc:
            continue
        low = desc.lower()
        if len(desc) < 10 or len(desc) > 500 or _is_header_low(low):
            continue
        row = {"section": sec[:60], "sub": sub, "desc": desc,
               "unit": unit or "бр", "qty": float(qty), "code": (str(code).strip() if code is not None else "")}
        own_price, own_total = None, None
        if isinstance(price, (int, float)) and price > 0:
            own_price = float(price)
        elif isinstance(total, (int, float)) and total > 0 and qty:
            own_price = round(float(total) / float(qty), 6)
            row["price_kind"] = "derived_unit"  # единична изведена от стойността
        if isinstance(total, (int, float)) and total > 0:
            own_total = float(total)
        if own_price is not None:
            if ccy == "BGN":
                own_price = round(own_price / EURBGN, 6)
                if own_total is not None:
                    own_total = round(own_total / EURBGN, 2)
            row["own_price"] = own_price
            if own_total is not None:
                row["own_total"] = own_total
            row.setdefault("price_kind", "explicit_unit")
            row["own_currency_note"] = ccy
        rows.append(row)
        if len(rows) >= max_rows:
            print(f"[pipeline] TRUNCATED at {max_rows} priced rows — останалите редове не влизат в пакета")
            return rows
    return rows


def parse_price_sheet(path, max_rows=MAX_ROWS):
    """User price sheets: desc + unit + qty + PRICE. Deterministic column split per row:
    price = ПОСЛЕДНОТО plausible число в реда; qty = предходното ако има. No invention."""
    import openpyxl
    out = []
    suf = Path(path).suffix.lower()
    iterator = None
    if suf == ".xlsx":
        iterator = _iter_sheet_rows(Path(path))
    elif suf == ".xls":
        iterator = _iter_sheet_rows(Path(path))
    elif suf == ".docx":
        iterator = _iter_docx_tables(Path(path))
    if iterator is None:
        return out
    for sec, vals in iterator:
        vals = list(vals)
        texts, nums = [], []
        for v in vals:
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    continue
                n = _bg_num(s)
                if n is not None and len(s) < 20 and not _norm_unit(s):
                    nums.append(n)
                elif len(s) >= 12:
                    texts.append(s)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                nums.append(float(v))
        if not texts or len(nums) < 2:
            continue
        desc = max(texts, key=len)
        low = desc.lower()
        if len(desc) > 500 or _is_header_low(low):
            continue
        qty = None
        for n in nums:
            if 0 < abs(n) < 1e8 and not (1900 <= n <= 2100):
                qty = n
                break
        price = nums[-1] if 0 < abs(nums[-1]) < 1e8 and not (1900 <= nums[-1] <= 2100) else None
        if qty is None or price is None:
            continue  # без и двете количество+цена не се записва нищо
        out.append({"desc": desc, "unit": "бр", "qty": qty, "price": price})
        if len(out) >= max_rows:
            print(f"[pipeline] TRUNCATED at {max_rows} price-sheet rows — останалите редове не влизат")
            return out
    return out


def parse_kss_docx(path, max_rows=MAX_ROWS):
    return _consume(_iter_docx_tables(path), max_rows)


_PDF_PAGE_MARK = re.compile(r"количествен|количествена\s+сметка|к\.\s*с\.|ксс", re.I)
_PDF_ROW = re.compile(
    r"^\s*(\d{1,4}(?:\.\d{1,3})?)[. )]?\s+"                       # №
    r"(.{12,200}?)"                                               # описание (lazy)
    r"\s+(м3|м2|м\.кв\.|кв\.м|л\.м\.?|м|бр\.|бр|компл\.?|кг|т|ч|час|кВт|точка|точки|брой|буч|м\.д\.)\s+"
    r"(\d[\d \u00a0]*(?:[.,]\d+)?)\s*$")                          # количество в края на реда


def parse_kss_pdf(path, max_rows=MAX_ROWS):
    """КСС embedded as text lines inside PDFs (teхспецификации/документации).
    Only pages that self-declare as количествена сметка are scanned; rows must
    match the strict „№ описание мярка количество" shape. No invention."""
    import pymupdf
    rows = []
    section = ""
    try:
        doc = pymupdf.open(str(path))
    except Exception:
        return rows
    for pno in range(doc.page_count):
        text = doc[pno].get_text("text")
        if not _PDF_PAGE_MARK.search(text):
            continue
        for line in text.splitlines():
            m = _PDF_ROW.match(line)
            if not m:
                t = line.strip()
                if 4 < len(t) < 80 and not re.search(r"\d", t) and any(ch.isupper() for ch in t):
                    section = t[:60]
                continue
            no, desc, unit, qtys = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
            low = desc.lower()
            if _is_header_low(low):
                continue
            qty = _bg_num(qtys)
            if qty is None or not _plausible_qty(qty):
                continue
            rows.append({"section": section or f"стр. {pno + 1}", "sub": "", "desc": desc,
                         "unit": unit, "qty": qty})
            if len(rows) >= max_rows:
                print(f"[pipeline] TRUNCATED at {max_rows} pdf rows — останалите редове не влизат в пакета")
                doc.close()
                return rows
    doc.close()
    return rows


# ---------------- deterministic cost matching ----------------
def _tokens(text):
    out = set()
    for w in re.split(r"[^0-9A-Za-zА-Яа-я]+", (text or "").lower()):
        w = _deconfuse(w)
        if len(w) >= 4 and w not in _STOP:
            out.add(w)
    return out


def _norm_unit(u):
    s = (u or "").strip().lower().replace(" ", "")
    return _UNIT_MAP.get(s, s if s and len(s) <= 12 else None)


_LAT2CYR = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р",
    "T": "Т", "X": "Х", "Y": "У", "a": "а", "c": "с", "e": "е", "h": "н", "i": "и", "k": "к",
    "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у"})


def _deconfuse(word):
    """Имена от СЕК справочника смесват латински глифове-двойници с кирилица
    („ОCТЪКЛЯВАHE“). Конвертираме към кирилица САМО при смесени думи —
    чисто латинските (PoE, LED) не се пипат."""
    if re.search(r"[А-Яа-я]", word):
        return word.translate(_LAT2CYR)
    return word


def _load_cost_rows(demo_dir):
    rows = []
    for name in ("costdb_seed_2026.json", "costdb_seed_sek_2026.json", "costdb_seed.json"):
        p = demo_dir / name
        if not p.exists():
            continue
        for it in json.loads(p.read_text(encoding="utf-8-sig")) or []:
            desc = it.get("desc") or it.get("name") or ""
            if not isinstance(desc, str) or len(desc) < 12:
                continue
            money = it.get("money") or {}
            amt = money.get("amount")
            if not isinstance(amt, (int, float)) or amt <= 0:
                continue
            cur = money.get("currency", "EUR")
            eur = amt / EURBGN if cur == "BGN" else amt
            rows.append({
                "desc": desc,
                "tokens": _tokens(desc + " " + (it.get("section") or "") + " " + (it.get("category") or "")),
                "unit": _norm_unit(it.get("unit") or ""),
                "unitEur": round(float(eur), 4),
                "vatIncluded": bool(money.get("vatIncluded")),
                "ref": (it.get("source") or (it.get("origin") or {}).get("ref") or "costdb")[:100],
            })
    return rows


def _build_index(cost_rows):
    idx = {}
    for r in cost_rows:
        for t in r["tokens"]:
            idx.setdefault(t, []).append(r)
    return idx


def match_cost(row, cost_rows, index):
    """Best candidate by weighted token overlap, unit-gated. Deterministic."""
    toks = _tokens(row["desc"])
    if not toks:
        return None, 0
    cand = {}
    for t in toks:
        for r in index.get(t, []):
            cand[id(r)] = cand.get(id(r), 0) + 1
    if not cand:
        return None, 0
    by_id = {id(r): r for r in cost_rows}
    u_norm = _norm_unit(row.get("unit"))
    best, best_s = None, 0
    for rid, s in cand.items():
        if s < 2:
            continue
        r = by_id[rid]
        score = s + (1.5 if u_norm and r["unit"] and r["unit"] == u_norm else 0)
        if score > best_s:
            best, best_s = r, score
    if best is None or best_s < 2.0:
        return None, 0
    return best, best_s

# ---------------- cost DB / matcher V2 ----------------

def _cost_row_view(r):
    """Convert canonical SQLite row to the historical cost-row shape + evidence."""
    try:
        extra = json.loads(r.get("extra_json") or "{}")
    except Exception:
        extra = {}
    bill_terms = [t for t in (extra.get("bill_terms") or []) if isinstance(t, str)]
    # The scored surface is domain-normalized (confusable Latin letters inside
    # Cyrillic words fold to Cyrillic — SEK catalogue writes "ОCТЪКЛЯВАHE") and
    # carries bill_terms; the displayed evidence keeps the real description.
    score_text = costdb._domain_norm(r.get("desc") or r.get("name") or "")
    if bill_terms:
        score_text += " " + " ".join(bill_terms)
    return {
        "id": r.get("id"),
        "desc": r.get("desc") or r.get("name") or "",
        "score_text": score_text,
        "bill_terms": bill_terms,
        "tokens": _tokens(r.get("desc") or r.get("name") or ""),
        "unit": r.get("unit"),
        "unitEur": round(float(r.get("amount_eur") or 0), 8),
        "vatIncluded": bool(r.get("vat_included")),
        "ref": (r.get("origin_ref") or r.get("id") or "costdb")[:150],
        "name": r.get("name") or r.get("desc") or "",
        "category": r.get("category") or "",
        "section": r.get("section") or "",
        "sourceKey": r.get("source_key") or "",
        "sourceType": r.get("origin_kind") or "",
        "sourceName": r.get("source_key") or "costdb",
        "asOf": r.get("as_of") or "",
        "status": r.get("status") or "active",
        "code": r.get("code") or "",
        "components": extra.get("components") or {},
        "spec": extra.get("spec") or {},
        "notes": extra.get("notes") or "",
        "priority": costdb.priority_for(r),
    }


def _deconfuse_text(text):
    """Word-wise Latin→Cyrillic confusable normalization (mixed words only)."""
    return " ".join(costdb._deconfuse_word(w) for w in re.split(r"(\W+)", text or ""))


def _tech_tokens(text):
    return {m.group(0).lower().replace(" ", "") for m in costdb.TECH_RE.finditer(_deconfuse_text(text))}


def _tech_family(tok):
    if re.search(r"(kw|mw|kva|квт|мвт|ква)$", tok):
        return "power"
    if re.search(r"(мм|см|м2|м3|m2|m3)$", tok):
        return "dim"
    if "x" in tok or "х" in tok or "×" in tok:
        return "cross"
    if tok.startswith(("ф", "ø")):
        return "diameter"
    if re.match(r"c\d+/\d+$", tok):
        return "concrete"
    return "other"


def _tech_conflict(qdesc, cdesc):
    """Same tech family present on BOTH sides with disjoint values = spec conflict.
    E.g. query 'чилър 130 kW' vs item 'чилър 60 kW' — different rating class, refuse.
    Absent tokens on either side never conflict (unknown is not a contradiction)."""
    q, c = {}, {}
    for t in _tech_tokens(qdesc or ""):
        q.setdefault(_tech_family(t), set()).add(t)
    for t in _tech_tokens(cdesc or ""):
        c.setdefault(_tech_family(t), set()).add(t)
    return any(q[fam] and c.get(fam) and not (q[fam] & c[fam]) for fam in q)


# ---------------- Construction signature (deterministic) ----------------
# Structured parse of a KCC/catalogue description BEFORE token canonicalization.
# Families keep subtypes: къртене and разбиване share family "remove" but remain
# distinct subtypes for ranking. Conflicts fire only on EXPLICIT mismatch —
# absent data is "unknown", never a contradiction.

_OP_FAMILY = {
    "remove": re.compile(r"демонтаж|демонтиране|разрушаване|разрушавене|разваляне|"
                         r"разбиване|къртене|очукване|очукане|изкърпване|демолиране|"
                         r"пробиване|отстраняване|подрязване|отсичане|изсичане", re.I),
    "supply": re.compile(r"доставка|доставяне|дост\.|снабдяване", re.I),
    "finish": re.compile(r"шпакловка|шпахловка|мазилка|боядисване|фугиране|фугираща|"
                         r"фугировк|грундиране|шлифоване|заличаване|лакиране", re.I),
    "install": re.compile(r"монтаж|монтиране|монт\.|полагане|положение|изпълнение|"
                          r"направа|изграждане|зидане|зидария|инсталиране|инсталация|"
                          r"облицовка|обшивка|залепване|запълване|монтиран|полаган|"
                          r"оформяне|обръщане|изолация|топлоизолац|хидроизолац|"
                          r"пароизолац|паропреград", re.I),
}

# Latin abbreviations appear in BOTH scripts (and mixed, e.g. "НРL" after
# deconfuse or typed Cyrillic ХПС). Per-letter classes cover every mixture.
_MATERIALS = {
    "xps": re.compile(r"[xх][pр][sѕс]|хпс|екструдиран\w*\s+полистирен|екструзиран\w*\s+полистирен", re.I),
    "eps": re.compile(r"[eе][pр][sѕс]|епс\b|експандиран\w*\s+полистирен|фибран", re.I),
    "полистирен": re.compile(r"полистирол|полистирен|пенополистирен|стиропор", re.I),
    "минвата": re.compile(r"(?:минерална|каменна|стъклена)\s+вата|минвата", re.I),
    "гипсокартон": re.compile(r"гипс[оа]картон|гипскартон|[aа]quapanel|аквапанел", re.I),
    "тухла": re.compile(r"тухла|тухлен|поротерм|[pр]orotherm|кирамичн\w*\s+блок", re.I),
    "бетон": re.compile(r"стоманобетон|бетон\b|бетонов\b", re.I),
    "гранитогрес": re.compile(r"гранитогрес", re.I),
    "фаянс": re.compile(r"фаянс|теракол", re.I),
    "pvc": re.compile(r"[pр]v[cс]|пвц", re.I),
    "алуминий": re.compile(r"алумини\w*|\b[aа]l\b\s*\w*\s*профил|еталбонд|композитн\w*\s+плоч", re.I),
    "латекс": re.compile(r"латекс|акрилат\w*", re.I),
    "hpl": re.compile(r"[hн][pр][lл]|хпл", re.I),
    "epdm": re.compile(r"[eе][pр]dм|гумена\s+настилк", re.I),
    "винил": re.compile(r"винил\w*", re.I),
    "полиетилен": re.compile(r"полиетилен", re.I),
    "силикон": re.compile(r"силикон", re.I),
    "битум": re.compile(r"битум\w*", re.I),
    "епоксид": re.compile(r"епоксид\w*|епомакс|[eе]pomax", re.I),
    "полиуретан": re.compile(r"полиуретан\w*|пу[ -]?лепил", re.I),
    "дърво": re.compile(r"дървен\w*|дървесин\w*", re.I),
    "метал": re.compile(r"метал\w*|стоманен\w*|стомана\b|желез\w*", re.I),
    "mdf": re.compile(r"[mм]d[fф]|мдф", re.I),
    "шперплат": re.compile(r"шперплат|пдч|[oо][sѕс][bв]|осб", re.I),
    "стъкло": re.compile(r"стъкл\w*|остъклен", re.I),
    "хидроизолация": re.compile(r"хидроизолац\w*", re.I),
    "балатум/линолеум": re.compile(r"балатум|линолеум", re.I),
}

_EXTRAS = ("лепил", "мреж", "дюбел", "грунд", "фуг", "кант", "анкер",
           "уплътнен", "профил", "релс", "носач", "окачва")

# Electrical domain guard: cables/fixtures/boards are a different product
# category from construction materials — a "силикон" cable is NOT silicone
# sealant, a "лустерклема" is NOT a door. One-sided electro = category conflict.
_ELECTRO_RE = re.compile(
    r"кабел|проводник|осветит|осветителн|полилей|лустерклема|клема|табло\b|"
    r"контакт|ключ за|шалтер|автомат\b|спот\b|[lл][eе]d\b|колонно\s+табло|"
    r"мультимедийн|електроуред|ел\.?\s*инсталац", re.I)


# Engineering spec channel: diameters, pressure classes, polymer grades and
# dimension pairs are the DISCRIMINATING data for pipe/HVAC/electro rows —
# they are <4 chars so the word tokenizer drops them (СК Ф63 → {изпразнител}).
# Extracted separately; disjoint non-empty sets on both sides = conflict,
# shared values = strong positive signal. Absence stays "unknown".
_INCH2DN = {0.5: 15, 0.75: 20, 1: 25, 1.25: 32, 1.5: 40, 2: 50,
            2.5: 65, 3: 80, 4: 100, 5: 125, 6: 150, 8: 200}
_DIA_RE = re.compile(r"(?:ф|ø|dn|дн)\s*(\d+(?:[.,]\d+)?)", re.I)
_INCH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[\"″]", re.I)
_PAIR_RE = re.compile(r"(?<![\d.,])(\d{1,4})\s*[xх×/]\s*(\d{1,4}(?:[.,]\d+)?)(?![\d.,])", re.I)
_PN_RE = re.compile(r"pn\s*(\d+)", re.I)
_PE_RE = re.compile(r"(?<![а-яa-z])(?:pe|ре)\s*-\s*(\d{2,3})", re.I)


def _spec_sig(norm_text):
    """Structured spec: dia={mm}, pair={sorted int pairs, cm-normalized},
    pn={bar}, pe={grade}. Inch marks convert to DN mm (2\" → 50)."""
    dia = {round(float(m.group(1).replace(",", ".")))
           for m in _DIA_RE.finditer(norm_text)}
    for m in _INCH_RE.finditer(norm_text):
        v = float(m.group(1).replace(",", "."))
        dia.add(_INCH2DN.get(v, v))
    pair = set()
    for m in _PAIR_RE.finditer(norm_text):
        a = float(m.group(1).replace(",", "."))
        b = float(m.group(2).replace(",", "."))
        pair.add(tuple(sorted(((round(a / 10, 1) if a >= 400 else a),
                               (round(b / 10, 1) if b >= 400 else b)))))
    pn = {int(m.group(1)) for m in _PN_RE.finditer(norm_text)}
    pe = {int(m.group(1)) for m in _PE_RE.finditer(norm_text)}
    return {"dia": dia, "pair": pair, "pn": pn, "pe": pe}


def _thickness_mm(norm_text):
    """Thickness/dimension values in mm (cm → ×10). All values are kept as a
    SET — conflict fires only on fully disjoint sets, so door dims 80/200 vs a
    candidate 80/200 still intersect; 8см vs 65мм correctly conflicts."""
    vals = set()
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(мм|mm|см|cm)\b", norm_text, re.I):
        v = float(m.group(1).replace(",", "."))
        if m.group(2).lower() in ("см", "cm"):
            v *= 10
        if v > 5000:
            continue  # sanity: nothing is a 5m thickness spec
        vals.add(round(v, 1))
    return vals


def _signature(text):
    """Deterministic construction signature from raw text — uses _clean+lower,
    NOT _domain_norm: deconfuse mangles abbreviations mid-word (HPL→НРL)."""
    norm = " " + costdb._clean(text or "").lower() + " "
    for rx, rep in costdb._ABBR_SWAPS:
        norm = rx.sub(rep, norm)
    ops = set()
    opsub = ""
    dom = None  # dominant op = earliest non-supply match (the head verb:
              # „фугировка НА зидария" is finish-of-masonry, not masonry work)
    best_pos = None
    for fam, rx in _OP_FAMILY.items():
        m = rx.search(norm)
        if m:
            ops.add(fam)
            if len(m.group(0)) > len(opsub):
                opsub = m.group(0).strip()
            if fam != "supply" and (best_pos is None or m.start() < best_pos):
                best_pos = m.start()
                dom = fam
    if dom is None and "supply" in ops:
        dom = "supply"
    return {"ops": ops, "op": dom, "opsub": opsub,
            "materials": {k for k, rx in _MATERIALS.items() if rx.search(norm)},
            "thickness": _thickness_mm(norm),
            "spec": _spec_sig(norm),
            "extras": {e for e in _EXTRAS if e in norm},
            "electro": bool(_ELECTRO_RE.search(norm))}


# Operation verbs that survive canonicalization — a match built ONLY on these
# is the „доставка и монтаж на душ" flood, not evidence of same work.
_GENERIC_OP_TOK = {"доставк", "монтаж", "полаган", "изпълнен", "направа",
                   "изработк", "изграждан", "разрушаван", "демонтаж", "къртен",
                   "очукван", "подготовк", "транспорт", "монтиран", "инсталац",
                   "доставя", "извършван", "снабдяван"}


def _extract_code(text):
    """Кодове: „СЕК 12.801“, „БЛ01.001“, „А-105-123“ — всякъде в текста, с буквен префикс."""
    m = re.search(r"\b([А-Яа-яA-Za-z]{1,4}\s*[-−]?\s*)?(\d{1,2}[.\-/]\d{1,4}(?:-\d+)?)\b", text or "", re.I)
    if not m:
        return ""
    return ((m.group(1) or "").strip() + m.group(2)).replace(" ", "").replace("−", "-")


def _stem_eq(a, b):
    """Cyrillic morphological-family match: exact or shared stem >=4 chars on
    tokens >=5 chars long — 'отпадъци/отпадаци', 'плочници/плоча', 'зидария/зидарии'.
    Exact-token scoring scored these as zero overlap and under-credited real matches."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5:
        return False
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 4


def _candidate_score(query_row, cand):
    """Evidence-driven pair score via the vendored ERP matcher.

    Returns (score_0_100, method, details) keeping the historical tuple shape.
    Scoring runs on ``score_text`` (description + curated bill_terms); the
    displayed evidence keeps the real catalogue description."""
    qdesc = costdb._domain_norm(query_row.get("desc") or query_row.get("name") or "")
    ctext = cand.get("score_text") or cand.get("desc") or ""
    code = (query_row.get("code") or _extract_code(qdesc) or "").replace(" ", "")
    cand_code = (cand.get("code") or "").replace(" ", "")
    ms = matcher.score_match(qdesc, ctext,
                             query_unit=query_row.get("unit"),
                             candidate_unit=cand.get("unit"))
    score = ms.confidence * 100.0
    method = "semantic"
    if code and cand_code and code.lower() == cand_code.lower():
        score = max(score, 100.0)
        method = "exact_code"
    elif ms.factors.get("exact"):
        method = "exact_description"
    elif costdb.exact_norm(qdesc) and costdb.exact_norm(qdesc) == costdb.exact_norm(cand.get("desc") or ""):
        score = max(score, matcher.HIGH_CONFIDENCE * 100.0)
        method = "exact_description"
    reasons = list(ms.reasons)
    return score, method, {
        "confidence_raw": ms.confidence,
        "reasons": reasons,
        "factors": dict(ms.factors),
        "token_overlap": ms.factors.get("term_overlap", 0.0),
        "query_coverage": ms.factors.get("query_coverage", 0.0),
        "cand_coverage": ms.factors.get("candidate_coverage", 0.0),
        "technical_overlap": 1.0 if ms.factors.get("spec_match") else 0.0,
        # Conflict vocabulary folded onto the vendored reasons: material and
        # thickness contradictions arrive as spec classes; action-family
        # contradictions as action_conflict; a candidate carrying specs the
        # bill never asked for is the spec-blind analogue.
        "material_conflict": False,
        "thickness_conflict": False,
        "op_conflict": "action_conflict" in reasons,
        "category_conflict": False,
        "spec_conflict": "spec_conflict" in reasons,
        "spec_blind": "candidate_extra_spec" in reasons,
        "op_mismatch": "action_conflict" in reasons,
        "unit_match": "unit_mismatch" not in reasons,
        "unit_factor": ms.factors.get("unit_factor", 1.0),
        "query_code": code,
        "candidate_code": cand_code,
        "source_priority": cand.get("priority"),
    }


def _candidate_score_legacy(query_row, cand):
    qdesc = query_row.get("desc") or query_row.get("name") or ""
    qtok = costdb.tokens(qdesc)
    # ALWAYS the canonical tokenizer for candidates too: _cost_row_view's cached
    # tokens come from the weaker local _tokens (no SYNONYMS), which made scoring
    # asymmetric — op-verb canonicalization applied to one side only.
    ctok = costdb.tokens(cand.get("desc") or "")
    if not qtok or not ctok:
        inter = overlap = qcover = ccover = 0.0
        matched_q = set()
    else:
        # fuzzy intersection: each query token matches at most one candidate token
        inter = 0
        used = set()
        matched_q = set()
        for t in qtok:
            for c in ctok:
                if c not in used and _stem_eq(t, c):
                    inter += 1
                    used.add(c)
                    matched_q.add(t)
                    break
        overlap = 2.0 * inter / (len(qtok) + len(ctok))
        qcover = inter / max(1, len(qtok))  # запитващият е покрит: асиметрично „коричното“"
        ccover = inter / max(1, len(ctok))  # кандидатът е съдържан в реда: кратък каталожен ред срещу дълго КСО описание
    qsig = _signature(qdesc)
    csig = _signature(cand.get("desc") or "")
    # Dominant-op logic: remove vs constructive = conflict; different
    # constructive families (install vs finish) = weak analogue signal.
    q_op, c_op = qsig.get("op"), csig.get("op")
    op_conflict = (q_op == "remove" and c_op not in (None, "remove")) or \
                  (c_op == "remove" and q_op not in (None, "remove"))
    op_mismatch = bool(q_op and c_op and q_op != c_op and not op_conflict)
    # Electrical component vs construction material: silicone-cable ≠ sealant.
    cat_conflict = bool(qsig["electro"] != csig["electro"] and
                        (qsig["electro"] or csig["electro"]))
    mat_shared = qsig["materials"] & csig["materials"]
    mat_conflict = bool(qsig["materials"] and csig["materials"] and not mat_shared)
    thick_conflict = bool(qsig["thickness"] and csig["thickness"]
                          and not (qsig["thickness"] & csig["thickness"]))
    # Engineering spec: ф/PN/PE/pair disjoint-on-both = dimensional conflict
    # (ф63 сифон ≠ ф100 сифон — real, review-tier; NOT a hard material error).
    spec_conflict = any(qsig["spec"][k] and csig["spec"][k]
                        and not (qsig["spec"][k] & csig["spec"][k])
                        for k in ("dia", "pair", "pn", "pe"))
    spec_shared = any(qsig["spec"][k] & csig["spec"][k]
                      for k in ("dia", "pair", "pn", "pe"))
    # Spec-blind winner: query carries engineering spec, candidate carries
    # none — absence never conflicts, but committing „СВТ 5х70" to a specless
    # RG6 rate is precisely the silent error this channel exists to prevent.
    spec_blind = (any(qsig["spec"][k] for k in ("dia", "pair", "pn", "pe"))
                  and not any(csig["spec"][k] for k in ("dia", "pair", "pn", "pe")))
    extras_shared = len(qsig["extras"] & csig["extras"])
    qtech = _tech_tokens(qdesc)
    ctech = _tech_tokens(cand.get("desc") or "")
    tech = (len(qtech & ctech) / max(1, len(qtech))) if qtech else 0.0
    qunit = costdb._norm_unit(query_row.get("unit"))
    cunit = costdb._norm_unit(cand.get("unit"))
    code = (query_row.get("code") or _extract_code(qdesc) or "").replace(" ", "")
    cand_code = (cand.get("code") or "").replace(" ", "")

    score = 0.0
    method = "semantic"
    if code and cand_code and code.lower() == cand_code.lower():
        score += 100.0
        method = "exact_code"
    elif costdb.exact_norm(qdesc) and costdb.exact_norm(qdesc) == costdb.exact_norm(cand.get("desc") or ""):
        score += 88.0
        method = "exact_description"
    score += overlap * 48.0
    score += qcover * 24.0  # short query covered by long candidate (asymmetric): count it
    score += ccover * 20.0  # short catalog item fully contained in a longer BoQ row
    score += tech * 18.0
    # Construction-signature terms: same work family + same material + shared
    # build extras raise; explicit material/thickness contradictions sink hard.
    score += 10.0 * bool(qsig["ops"] & csig["ops"])
    score += 16.0 * bool(mat_shared)
    score += min(8.0, extras_shared * 2.5)
    score += 14.0 * bool(spec_shared)  # shared ф/PN/pair — the discriminating data agrees
    if op_conflict:
        score -= 30.0
    if mat_conflict:
        score -= 34.0
    if thick_conflict:
        score -= 30.0
    if spec_conflict:
        score -= 30.0
    if cat_conflict:
        score -= 36.0
    if op_mismatch:
        score -= 10.0
    if qunit and cunit and qunit == cunit:
        score += 14.0
    elif qunit and cunit and qunit != cunit:
        score -= 20.0
    # Operation-class conflict: removal verbs canonicalize to „разрушаване" in
    # SYNONYMS, so a row that is demolition work and a candidate that is install
    # work (or vice versa) is an op-class mismatch — penalized, never excluded.
    q_rem = "разрушаване" in qtok
    c_rem = "разрушаване" in ctok
    if q_rem != c_rem:
        score -= 14.0
    # Generic-op flood guard: if the ONLY matched query tokens are operation
    # verbs (доставк/монтаж survive canonicalization as content), the match is
    # noise — „доставка и монтаж на намалител 75/50" must NOT score 91 against
    # „доставка и монтаж на душ". Cap below the noise floor.
    if method == "semantic" and matched_q and not (matched_q - _GENERIC_OP_TOK):
        score = min(score, 14.0)
    score += min(8.0, float(cand.get("priority") or 40.0) / 12.5)
    asof = str(cand.get("asOf") or "")
    if asof.startswith("2026"):
        score += 3.0
    elif asof.startswith("2025"):
        score += 1.5
    return score, method, {
        "token_overlap": round(overlap, 4),
        "query_coverage": round(qcover, 4),
        "cand_coverage": round(ccover, 4),
        "technical_overlap": round(tech, 4),
        "op_family": sorted(qsig["ops"]), "cand_op_family": sorted(csig["ops"]),
        "materials": sorted(qsig["materials"]), "cand_materials": sorted(csig["materials"]),
        "material_conflict": mat_conflict, "thickness_conflict": thick_conflict,
        "op_conflict": op_conflict, "category_conflict": cat_conflict,
        "spec_conflict": spec_conflict, "spec_blind": spec_blind, "op_mismatch": op_mismatch,
        "unit_match": bool(qunit and cunit and qunit == cunit),
        "query_code": code,
        "candidate_code": cand_code,
        "source_priority": cand.get("priority"),
    }


_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(?:смр[\s\-–—]*разпределени\w*|спецификаци\w*|също[,\s])", re.I)


def _apply_unit_conversion(cand, qunit):
    """Reprice ``cand`` per the query unit via ``matcher.unit_rate_factor``.

    Same-dimension different-magnitude units (€/т on a кг line, €/100м3 on a м3
    line) score as compatible but MUST be converted before the price is spent —
    870 €/т is 0.87 €/кг. Returns ``None`` when the units differ and no honest
    conversion exists (unknown unit, or a dimension with no pinned magnitude
    like day↔hour): spending an unconvertible rate would fabricate the price."""
    if not qunit:
        return cand
    f = matcher.unit_rate_factor(cand.get("unit"), qunit)
    if f is None:
        if not (cand.get("unit") or "").strip():
            return cand  # corpus row carries no unit — unit_match already allowed it
        if costdb._norm_unit(cand.get("unit")) == costdb._norm_unit(qunit):
            return cand  # same spelling, factor machinery just does not know it
        return None
    if f == 1:
        return cand
    out = dict(cand)
    out["unitEur"] = round(float(cand.get("unitEur") or 0) * float(f), 8)
    out["unit"] = qunit
    out["unit_converted"] = {"from": cand.get("unit"), "to": qunit,
                             "factor": float(f)}
    return out


def match_cost_v2(row, db_path, limit=40, accept_pending=False):
    """Tiered deterministic matcher. Returns (best_or_none, score, evidence).
    accept_pending=True lets pending_review corpus rows commit through the SAME
    acceptance gate (score/margin/conflict checks identical). Used by the §3B
    estimation path — the corpus' pending prices are real reference prices the
    operator owns; the evidence marks them 'reference', never 'observed'.

    Commit gate (vendored matcher): the winner must be an exact code/description
    hit or clear the HIGH_CONFIDENCE floor, with no unit conflict — AND must be
    unambiguous (no equal-footing rival at a materially different price, ~5%)
    and undiluted (no >=4 content words the evidence never explains). Structural
    rows (СМР-РАЗПРЕДЕЛЕНИЕ …, спецификация …) never price — they are classes,
    not items. A query-code row in the corpus is evidence/anchor only
    (same_code flag), never spent: spending „KSO 1.1" as the price for
    „KSO 1.1" collapses when two rows share a code."""
    qdesc = row.get("desc") or ""
    if _STRUCTURAL_LINE_RE.match(qdesc):
        return None, 0.0, {"method": "section_header", "confidence": "none",
                           "score": 0.0, "margin": 0.0, "candidate_count": 0,
                           "pending_count": 0, "top_candidates": [],
                           "features": {"structural": True}}
    query_code = row.get("code") or _extract_code(qdesc)
    raw = costdb.search_candidates(db_path, qdesc, row.get("unit"), limit=limit, include_pending=True, code=query_code or None)
    candidates = [_cost_row_view(r) for r in raw]
    scored, pending_scored = [], []
    for cand in candidates:
        if cand.get("status") == "retired":
            continue
        s, method, details = _candidate_score(row, cand)
        (pending_scored if cand.get("status") != "active" else scored).append((s, method, details, cand))
    # Deterministic ordering — score, then equal-evidence preference for
    # verified sources, then source priority, then id (stable margin math).
    scored.sort(key=lambda x: (x[0], x[3].get("status") == "active",
                               x[3].get("priority", 0), x[3].get("id") or ""), reverse=True)
    pending_scored.sort(key=lambda x: (x[0], x[3].get("priority", 0),
                                       x[3].get("id") or ""), reverse=True)
    if not scored and not pending_scored:
        return None, 0.0, {"method": "no_candidates", "confidence": "none",
                           "score": 0.0, "margin": 0.0, "candidate_count": 0,
                           "pending_count": 0, "top_candidates": [], "features": {}}

    _METHOD_RANK = {"exact_code": 3, "exact_description": 2}

    def _judge(pool):
        best = pool[0]
        second = pool[1] if len(pool) > 1 else None
        # Margin counts only against the first candidate with a MATERIALLY
        # different price (~5%): duplicates quoting the same price answer the
        # same way — they raise confidence, they must not read as ambiguity.
        best_price = best[3].get("unitEur") or 0.0
        differing = next((x for x in pool[1:]
                          if abs((x[3].get("unitEur") or 0) - best_price) > max(0.005, abs(best_price) * 0.05)), None)
        margin = best[0] - (differing[0] if differing else 0.0)
        price_agreement = differing is None and second is not None
        unit_conflict = bool(row.get("unit") and (best[3].get("unit_norm") or best[3].get("unit"))
                             and best[1] != "exact_code"
                             and not best[2].get("unit_match", True))
        tech_conflict = bool((best[1] != "exact_code"
                              and _tech_conflict(qdesc, best[3].get("desc") or ""))
                             or "spec_conflict" in best[2].get("reasons", ())
                             or "action_conflict" in best[2].get("reasons", ()))
        spec_conflict = (tech_conflict or best[2].get("material_conflict")
                         or best[2].get("thickness_conflict") or best[2].get("op_conflict")
                         or best[2].get("category_conflict") or best[2].get("spec_conflict"))
        # Ambiguity: a rival on identical footing (score, method rank, spec
        # footing, matched-token breadth) at a different price means the
        # evidence cannot pick — committing would fabricate certainty.
        best_spec = bool(best[2].get("factors", {}).get("spec_match"))
        best_mt = float(best[2].get("factors", {}).get("matched_tokens") or 0)

        def _is_rival(x):
            if x[0] < best[0]:
                return False
            if _METHOD_RANK.get(x[1], 1) < _METHOD_RANK.get(best[1], 1):
                return False
            if best_spec and not x[2].get("factors", {}).get("spec_match"):
                return False
            if float(x[2].get("factors", {}).get("matched_tokens") or 0) < best_mt:
                return False
            return abs((x[3].get("unitEur") or 0) - best_price) > max(0.005, abs(best_price) * 0.05)

        ambiguous = any(_is_rival(x) for x in pool[1:])
        # Dilution: 4+ content words no candidate explains = composite or
        # noise-flooded line — evidence for a fragment is not evidence for it.
        q_content = (set(matcher.canonical_tokens(costdb._domain_norm(qdesc)))
                     - matcher._PROCESS_CONCEPTS - matcher._GENERIC_TERMS)
        mt_factor = best[2].get("factors", {}).get("matched_tokens")
        matched = len(q_content) if mt_factor is None else int(mt_factor)
        diluted = (len(q_content) - matched) >= 4
        acceptable = (
            best[1] == "exact_code" and not unit_conflict or
            best[1] == "exact_description" and not unit_conflict and not spec_conflict or
            best[0] >= matcher.HIGH_CONFIDENCE * 100.0
            and not unit_conflict and not spec_conflict
            and not ambiguous and not diluted)
        confidence = "high" if acceptable else \
            "medium" if best[0] >= matcher.REVIEW_CONFIDENCE * 100.0 else "low"
        weak = bool(best[2].get("spec_blind") or best[2].get("op_mismatch")
                    or ambiguous or diluted
                    or (second is not None and margin < 4.0 and not price_agreement))
        return best, margin, tech_conflict, acceptable, confidence, ambiguous, diluted, weak

    def _evidence(best, margin, tech_conflict, confidence, pending_best,
                  ambiguous=False, diluted=False, weak=False):
        return {
            "method": best[1],
            "score": round(best[0], 2),
            "margin": round(margin, 2),
            "confidence": confidence,
            "tech_conflict": tech_conflict,
            "ambiguous": ambiguous,
            "diluted": diluted,
            "weak": weak,
            "features": best[2],
            "candidate_count": len(scored),
            "pending_count": len(pending_scored),
            "reference": bool(pending_best),
            "top_candidates": [
                {"id": x[3].get("id"), "desc": x[3].get("desc"), "unit": x[3].get("unit"),
                 "score": round(x[0],2), "source": x[3].get("sourceKey"), "ref": x[3].get("ref"),
                 "status": x[3].get("status"), "unitEur": x[3].get("unitEur"),
                 "code": x[3].get("code") or "",
                 "same_code": bool(query_code and x[3].get("code") and
                                   query_code.replace(" ", "").lower() ==
                                   x[3]["code"].replace(" ", "").lower()),
                 "conflict": bool(x[2].get("material_conflict")
                                  or x[2].get("op_conflict") or x[2].get("category_conflict")),
                 "specConflict": bool(x[2].get("thickness_conflict") or x[2].get("spec_conflict")),
                 "specBlind": bool(x[2].get("spec_blind")),
                 "weak": bool(x[2].get("op_mismatch") or x[2].get("spec_blind")),
                 "unitMatch": bool(x[2].get("unit_match", True))}
                for x in sorted(scored + pending_scored,
                                key=lambda x: (x[0], x[3].get("priority", 0)), reverse=True)[:5]
            ],
        }

    _PENDING_TOP = lambda x: {
        "id": x[3].get("id"), "desc": x[3].get("desc"), "unit": x[3].get("unit"),
        "score": round(x[0], 2), "source": x[3].get("sourceKey"), "ref": x[3].get("ref"),
        "status": x[3].get("status"), "unitEur": x[3].get("unitEur"),
        "conflict": bool(x[2].get("material_conflict")
                         or x[2].get("op_conflict") or x[2].get("category_conflict")),
        "specConflict": bool(x[2].get("thickness_conflict") or x[2].get("spec_conflict")),
        "specBlind": bool(x[2].get("spec_blind")),
        "weak": bool(x[2].get("op_mismatch")),
        "unitMatch": bool(x[2].get("unit_match", True))}

    if scored:
        best, margin, tech_conflict, acceptable, confidence, ambiguous, diluted, weak = _judge(scored)
        if acceptable:
            priced_cand = _apply_unit_conversion(best[3], row.get("unit"))
            if priced_cand is None:
                ev = _evidence(best, margin, tech_conflict, "low", False,
                               ambiguous, diluted, True)
                ev["gate"] = "unit_no_conversion"
                return None, best[0], ev
            ev = _evidence(best, margin, tech_conflict, confidence, False,
                           ambiguous, diluted, weak)
            if priced_cand.get("unit_converted"):
                ev["unit_converted"] = priced_cand["unit_converted"]
            return priced_cand, best[0], ev
    if accept_pending and pending_scored:
        best, margin, tech_conflict, acceptable, confidence, ambiguous, diluted, weak = _judge(pending_scored)
        if acceptable:
            priced_cand = _apply_unit_conversion(best[3], row.get("unit"))
            if priced_cand is None:
                ev = _evidence(best, margin, tech_conflict, "low", True,
                               ambiguous, diluted, True)
                ev["gate"] = "unit_no_conversion"
                return None, best[0], ev
            ev = _evidence(best, margin, tech_conflict, confidence, True,
                           ambiguous, diluted, weak)
            if priced_cand.get("unit_converted"):
                ev["unit_converted"] = priced_cand["unit_converted"]
            return priced_cand, best[0], ev
    if not scored:
        return None, 0.0, {
            "method": "pending_only",
            "candidates": [],
            "candidate_count": 0,
            "pending_count": len(pending_scored),
            "top_candidates": [_PENDING_TOP(x) for x in pending_scored[:5]],
        }
    best, margin, tech_conflict, _acc, confidence, ambiguous, diluted, weak = _judge(scored)
    return None, best[0], _evidence(best, margin, tech_conflict, confidence, False,
                                  ambiguous, diluted, weak)


# ---------------- LLM advisory pass ----------------
def llm_verify(base_url, model, api_key, pack, timeout=120):
    """Advisory only: suspicious matches + short summary. Never mutates numbers."""
    flagged = [l for l in pack["boq"] if l["rule"] == "EST"][:25]
    matched = [l for l in pack["boq"] if l["rule"] == "CSV"][:25]
    def fmt(l):
        return f"- [{l['rule']}] {l['desc'][:90]} | {l['qty']} {l['unit']} | {l.get('unitEur')} € | {(l.get('note') or '')[:90]}"
    prompt = ("Ти си проверяващ инженер. Дадена е извлечена КСС от тръжна документация с машинно остойностяване "
              "от ЛОКАЛНА себестойна база. CSV = намерен локален разход; EST = няма локална цена (чака човек). "
              "Отговори КРАТКО на български: 1) до 5 най-подозрителни CSV съпоставяния; 2) до 3 позиции EST, "
              "които изглеждат лесни за оценка от семантиката; 3) едно изречение за цялостното качество на извличането.")
    msgs = [{"role": "system", "content": prompt},
            {"role": "user", "content":
             "CSV съпоставени:\n" + ("\n".join(fmt(l) for l in matched) or "(няма)") +
             "\n\nEST без цена:\n" + ("\n".join(fmt(l) for l in flagged) or "(няма)")}]
    t0 = time.time()
    text = llm.chat_text(base_url, model, msgs, api_key=api_key, temperature=0.2, max_tokens=700, timeout=timeout)
    return {"ok": True, "model": model, "seconds": round(time.time() - t0, 1), "text": text[:4000]}


def _llm_pass(llm_settings, pack):
    """Optional advisory: only when an OK connection AND a resolvable model exist."""
    base, model = llm_settings.get("base_url"), llm_settings.get("model")
    if not base or not model:
        pack["llm"] = {"ok": False, "error": "няма избран/записан модел — виж LLM таб", "text": ""}
        return
    try:
        ok, _, resolved = llm.test_conn(base, model, api_key=llm_settings.get("api_key") or None)
        if not ok:
            pack["llm"] = {"ok": False, "error": "LLM връзката не е OK — виж LLM таб", "text": ""}
            return
        pack["llm"] = llm_verify(base, resolved or model, llm_settings.get("api_key") or None, pack)
    except Exception as ex:
        pack["llm"] = {"ok": False, "error": str(ex)[:300], "text": ""}


# ---------------- main ----------------
def process_tender(rec, demo_dir, proc_dir, llm_settings=None, cancel_cb=None):
    """Returns {'ok':bool,'error':str|None,'cancelled':bool,'pack':dict|None}.
    Never invents numbers. cancel_cb() truthy -> cooperative stop, no pack written."""
    global _cancel_cb
    _cancel_cb = cancel_cb
    tid = rec.get("id")
    proc_dir.mkdir(parents=True, exist_ok=True)
    files_dir = proc_dir / "files" / str(tid)
    st = {"phase": "download", "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    dbp = costdb.db_path_for(demo_dir)
    run_id = None
    try:
        run_id = costdb.run_open(dbp, tid)
        costdb.run_event(dbp, run_id, "ACQUIRED", f"tender {tid}")
    except Exception:
        pass
    _log(f"start tender {tid}")
    try:
        _check_cancel()
        docs = download_docs(rec, files_dir)
        st["download"] = f"{sum(1 for d in docs if d.get('localPath'))}/{len(docs)} файла"
        if run_id:
            costdb.run_event(dbp, run_id, "DOWNLOADED", st["download"])
        kss_paths = pick_kss_paths(docs)
        # Multi-КСС tenders (етапи/части): UNION every КСС-shaped spreadsheet — max-wins
        # silently dropped whole etaps (600799 lesson). Superseded versions of the same
        # КСС (old vs new ЦАИС docId in the filename) collapse to the newest — old stays
        # на диск за одит, в обхвата не влиза.
        def _stem_key(nm):
            stem = re.sub(r"\s*\(\d{6,}\).*?(\.[a-z]+)?$", "", nm, flags=re.I).strip().lower()
            return stem
        def _doc_no(nm):
            m = re.findall(r"\((\d{6,})\)", nm)
            return max(int(x) for x in m) if m else 0
        sheet_by_stem, other_kss = {}, []
        for name, p in kss_paths:
            if p.suffix.lower() in (".xlsx", ".xls"):
                prev = sheet_by_stem.get(_stem_key(name))
                if prev is None or _doc_no(name) > _doc_no(prev[0]):
                    sheet_by_stem[_stem_key(name)] = (name, p)
            else:
                other_kss.append((name, p))
        sheets = sorted(sheet_by_stem.values(), key=lambda np: -_doc_no(np[0]))
        rows, used_src = [], ""
        if sheets:
            names_used = []
            for name, p in sheets:
                _check_cancel()
                try:
                    got = parse_kss_priced(p)
                    alt = parse_kss(p)  # headerless heuristics (golden 03/15) outrank a weak header lock
                    if len(alt) > len(got):
                        got = alt
                except Exception as ex:
                    _log(f"parse fail {p.name}: {ex}")
                    continue
                stem_tag = re.sub(r"\s*\[[^]]*\]\s*", " ", re.sub(r"\(\d{6,}\)", "", name)).strip()[:40]
                for r in got:
                    r = dict(r)
                    r["src_file"] = name
                    if not r.get("sub"):
                        r["sub"] = stem_tag
                    rows.append(r)
                names_used.append(f"{name[:36]}({len(got)})")
            used_src = " + ".join(names_used)
        for name, p in other_kss:
            if sheets:
                break  # spreadsheets carried the scope; pdf/docx only when no sheets exist
            _check_cancel()
            try:
                suf = p.suffix.lower()
                got = parse_kss_docx(p) if suf == ".docx" else parse_kss_pdf(p)
            except Exception as ex:
                _log(f"parse fail {p.name}: {ex}")
                continue
            if len(got) > len(rows):
                rows, used_src = got, name
        st["kss"] = f"{len(rows)} позиции от „{used_src}“" if rows else "не е намерена КСС таблица"
        if run_id:
            costdb.run_event(dbp, run_id, "PARSED", st["kss"])
        if not rows:
            # Tenders normally ship NO КСС (scope lives in specs/measurements) — this is a
            # guided intake state, NOT a crash. No pack (never-empty rule); run closes NO_SCOPE;
            # the UI takes the user straight to the scope-intake actions.
            st["code"] = "NO_SCOPE"
            st["error"] = ("няма извлечен обхват (КСС редове) от документите на поръчката")
            st["guidance"] = [
                "прикачи ЦАИС „Експорт“ (КС файлът) — бутон в екрана на поръчката",
                "качи своя КСС/количествена таблица (тя става документ-обхват на поръчката)",
                "провери registry.json: документите на поръчката може да са скрити/в папки",
            ]
            if run_id:
                costdb.run_event(dbp, run_id, "NO_SCOPE", st["error"])
                costdb.run_close(dbp, run_id, "NO_SCOPE", st["error"])
            st["done"] = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                (proc_dir / f"{tid}.error.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
            _log(f"tender {tid}: NO_SCOPE — guided intake (no pack written)")
            _cancel_cb = None
            return {"ok": False, "code": "NO_SCOPE", "error": st["error"],
                    "guidance": st["guidance"], "state": st}
        db_path = dbp
        sync_info = costdb.sync_json_sources(demo_dir, db_path)
        env_id = costdb.ensure_env_version(db_path, note=f"run for tender {tid}")
        if run_id:
            costdb.pin_run_env(db_path, run_id, env_id)
        st["costdb"] = f"SQLite {db_path} · {costdb.stats(db_path)['active']} активни / {costdb.stats(db_path)['pending_review']} за преглед"
        boq, n_csv = [], 0
        match_methods = {}
        for i, r in enumerate(rows):
            if i % 25 == 0:
                _check_cancel()
            line = {"key": f"R{i+1}", "row": i + 1, "no": f"{i+1}.0", "section": r["section"],
                    "sub": r.get("sub") or "", "desc": r["desc"], "unit": r["unit"], "qty": round(r["qty"], 3),
                    "code": r.get("code") or "", "tone": "web", "flag": "", "note": ""}
            if r.get("own_price"):
                # Priced КСС document: its unit price is OBSERVED company evidence
                # (owner 2026-09-06/07: account docs win; corpus fills only their blanks).
                net = float(r["own_price"])
                n_csv += 1
                match_methods["document_price"] = match_methods.get("document_price", 0) + 1
                note_conflict = ""
                line.update({"rule": "CSV", "ruleLabel": "Документална цена (OBSERVED)",
                             "unitEur": round(net, 4), "sumEur": round(net * r["qty"], 2),
                             "matUnitEur": round(net, 4),
                             "laborHoursUnit": 0, "laborEur": 0, "equipmentEur": 0,
                             "match": {"method": "document_price", "confidence": "high",
                                       "evidenceState": "OBSERVED", "source_file": r.get("src_file") or ""},
                             "source": {"id": None, "sourceKey": "user_doc", "sourceType": "company_actual",
                                        "ref": r.get("src_file") or "прикачен документ", "asOf": None,
                                        "status": "active"},
                             "note": f"документална цена · {r.get('src_file') or '?'} · OBSERVED{note_conflict}"})
                try:
                    costdb.record_match(db_path, tid, line, None, "document_price",
                                        0, 0, "high", line["match"])
                except Exception as mex:
                    _log(f"match-record fail {line['key']}: {mex}")
            else:
                best, score, evidence = match_cost_v2(r, db_path)
                if best:
                    net = best["unitEur"] / 1.2 if best["vatIncluded"] else best["unitEur"]
                    n_csv += 1
                    match_methods[evidence["method"]] = match_methods.get(evidence["method"], 0) + 1
                    comps = best.get("components") or {}
                    line.update({"rule": "CSV", "ruleLabel": "Локален разход (costdb)",
                                 "unitEur": round(net, 4), "sumEur": round(net * r["qty"], 2),
                                 "matUnitEur": round(float(comps.get("materialsEur") or net), 4),
                                 "laborHoursUnit": 0,
                                 "laborEur": round(float(comps.get("laborEur") or 0), 4),
                                 "equipmentEur": round(float(comps.get("equipmentEur") or 0), 4),
                                 "match": evidence,
                                 "source": {"id": best.get("id"), "sourceKey": best.get("sourceKey"),
                                            "sourceType": best.get("sourceType"), "ref": best.get("ref"),
                                            "asOf": best.get("asOf"), "status": best.get("status")},
                                 "note": f"costdb ← {best['ref']} · {best.get('sourceKey','?')} · {evidence['method']} · score {score:.1f} · margin {evidence['margin']:.1f}"})
                    try:
                        costdb.record_match(db_path, tid, line, best.get("id"), evidence["method"],
                                            score, evidence["margin"], evidence.get("confidence") or "", evidence)
                    except Exception as mex:
                        _log(f"match-record fail {line['key']}: {mex}")
                else:
                    hint = ""
                    try:
                        anchors = costdb.search_candidates(db_path, r["desc"], r.get("unit"), limit=6,
                                                           include_pending=True,
                                                           origin_kind=("supplier_web_anchor", "reference_web", "imported_json"))
                        scored_a = []
                        for a in anchors:
                            av = _cost_row_view(a)
                            s, _m, _d = _candidate_score(r, av)
                            scored_a.append((s, av))
                        scored_a.sort(key=lambda x: -x[0])
                        if scored_a and scored_a[0][0] >= 30:
                            av = scored_a[0][1]
                            hint = (f" | web-анкер: {av['ref']} ≈ {av['unitEur']:.2f} €/{r['unit']} "
                                    f"(пазарен ориентир, pending_review — НЕ е себестойност)")
                    except Exception as wex:
                        _log(f"web-anchor hint fail {line['key']}: {wex}")
                    line.update({"rule": "EST", "ruleLabel": "HUMAN_INPUT_REQUIRED", "tone": "est",
                                 "unitEur": 0, "sumEur": 0, "matUnitEur": 0, "laborHoursUnit": 0,
                                 "laborEur": 0, "flag": "COST_NOT_FOUND", "match": evidence,
                                 "note": "Няма достатъчно надежден локален match — цената НЕ е измислена; изисква човек." + hint})
                    if telemetry:
                        try:
                            telemetry.log_event(
                                telemetry.EventType.HUMAN_GATE_CREATED,
                                message=f"Human review gate required for '{line['key']}': {line['desc'][:60]}",
                                tender_id=tid,
                                run_id=run_id,
                                payload={"boq_key": line["key"], "desc": line["desc"], "unit": line["unit"], "hint": hint},
                                db_path=dbp
                            )
                        except Exception:
                            pass
            boq.append(line)
        if run_id:
            costdb.run_event(dbp, run_id, "MATCHED", f"{n_csv}/{len(boq)} priced · методи={match_methods}")
        total = round(sum(l["sumEur"] for l in boq), 2)
        cap = rec.get("estValue") or 0
        vat = round(total * 0.2, 2)
        sections = {}
        for l in boq:
            sections[l["section"]] = round(sections.get(l["section"], 0) + l["sumEur"], 2)
        pricing = {"tenderId": tid, "currency": "EUR", "vatRate": 0.2, "capExclVat": cap,
                   "justificationThreshold20pct": round(cap * 0.8, 2) if cap else 0,
                   "totalExclVat": total, "vat": vat, "totalInclVat": round(total + vat, 2),
                   "marginUnderCap": round(cap - total, 2) if cap else 0,
                   "laborPoolHours": 0, "laborRateEur": 30.0, "laborPoolEur": 0,
                   "embeddedLaborHours": 0, "embeddedLaborEur": 0,
                   "restLaborHours": 0, "restLaborEur": 0,
                   "sectionTotals": sections, "sectionLabor": {k: 0 for k in sections},
                   "costEnvVersion": env_id,
                   "costVersion": f"SQLite DB v{costdb.DB_VERSION} · env#{env_id} · canonical local cost environment"}
        rules = [
            {"id": "GEN-01", "group": "КСС", "rule": "КСС таблица извлечена от документите",
             "source": used_src or "—", "check": st["kss"], "status": "pass" if rows else "manual"},
            {"id": "GEN-02", "group": "Остойностяване", "rule": "Само активната SQLite costdb; pending_review/web не влиза в аритметиката",
             "source": "costdb", "check": f"{n_csv}/{len(boq)} позиции с локална цена; {len(boq)-n_csv} чакат човек; методи={match_methods}",
             "status": "pass" if boq and n_csv == len(boq) else "manual"},
            {"id": "GEN-03", "group": "Таван", "rule": "Обща цена без ДДС ≤ прогнозна стойност",
             "source": "изчислено", "check": (f"{total:.2f} € ≤ {cap:.2f} €" if cap else "няма прогнозна"),
             "status": (("pass" if total <= cap else "manual") if cap and rows else "manual")},
            {"id": "GEN-04", "group": "Документи", "rule": "Инвентар на приложените документи",
             "source": "ЦАИС ЕОП", "check": st["download"], "status": "pass"},
        ]
        clarifs = [{"id": f"СЪОБЩЕНИЕ-{i+1}", "title": a.get("title") or "—", "date": a.get("created") or "",
                    "source": "публични съобщения към поръчката", "qa": [], "impact": []}
                   for i, a in enumerate(rec.get("announcements") or [])]
        pack = {"tenderId": tid, "generatedAt": st["at"], "pipe": st, "boq": boq,
                "pricing": pricing, "rules": rules, "documents": docs, "clarifications": clarifs,
                "llm": {"ok": False, "text": "", "error": "не е стартиран"}}
        _check_cancel()
        _llm_pass(llm_settings or {}, pack)
        rules.append({"id": "GEN-05", "group": "LLM", "rule": "LLM проверка (съветник, не променя числа)",
                      "source": (llm_settings or {}).get("base_url") or "—",
                      "check": "доклад генериран" if pack["llm"].get("ok") else f"пропуснато: {pack['llm'].get('error', '?')}",
                      "status": "pass" if pack["llm"].get("ok") else "manual"})
        if run_id:
            costdb.run_event(dbp, run_id, "PRICED", f"Σ {total:.2f} € без ДДС")
            costdb.run_event(dbp, run_id, "REVIEWED", "LLM: " + ("ok" if pack["llm"].get("ok") else "пропуснат"))
        st["done"] = time.strftime("%Y-%m-%d %H:%M:%S")
        # flywheel: fingerprint + similarity (project memory, cheap start)
        try:
            toks = set()
            for l in boq:
                toks |= costdb.tokens(l["desc"])
            toks = set(list(toks)[:600])
            costdb.save_fingerprint(dbp, tid, toks, len(boq), n_csv, total)
            pack["similar"] = costdb.similar_tenders(dbp, toks, exclude_id=tid)
        except Exception as fex:
            _log(f"fingerprint fail: {fex}")
        # Geometry evidence stage (spatial v2, contract §3/§4 — additive): DXF/IFC/PDF/DOCX
        # under files/<tid>/ -> sha256-cached extraction -> authority fusion ->
        # pack["geometry_evidence"]. No candidates -> skipped entirely (no key added).
        # ANY failure logs and continues legacy schematic behavior — never fails the run.
        try:
            _geom_cands = [p for _, p in pick_geometry_paths(docs)]
            if _geom_cands:
                _gev = fuse_geometry_files(_geom_cands, Path(demo_dir).parent / "geometry_cache")
                if _gev:
                    pack["geometry_evidence"] = _gev
                    st["geometry"] = f"{len(_gev.get('sources') or [])} геометрични източника"
                    if run_id:
                        try:
                            costdb.run_event(dbp, run_id, "GEOMETRY", st["geometry"])
                        except Exception:
                            pass
        except Exception as gex:
            _log(f"geometry stage failed (loud, non-fatal): {gex}")
        # Gate 1 #1: the pack is a runtime contract — violations are loud, never written silently.
        _validate.assert_pack(pack)
        if not boq:
            raise RuntimeError("refusing to write an empty tender pack (0 BOQ rows)")
        out = proc_dir / f"{tid}.json"
        # Input-files signature: the /process cache may only serve a pack whose evidence
        # files are exactly the ones it was built from (attach → signature changes → reprocess).
        fdir = proc_dir / "files" / str(tid)
        pack["filesSig"] = sorted(f.name for f in fdir.glob("*") if f.is_file()) if fdir.exists() else []
        # Atomic write (B1): a torn/interrupted write must never land as the canonical
        # pack — temp file in the same dir, then os.replace.
        tmp = proc_dir / f".{tid}.json.tmp"
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)
        if telemetry:
            try:
                telemetry.log_event(
                    telemetry.EventType.ARTIFACT_CREATED,
                    message=f"Created processed pack artifact: {out.name} ({len(boq)} rows, {total:.2f} EUR)",
                    tender_id=tid,
                    run_id=run_id,
                    payload={"path": str(out), "rows": len(boq), "priced_rows": n_csv, "total_excl_vat": total},
                    db_path=dbp
                )
            except Exception:
                pass
        if run_id:
            try:
                costdb.run_event(dbp, run_id, "PACKED", str(out))
                costdb.run_close(dbp, run_id, "PACKED", f"{len(boq)} rows / {n_csv} priced / {total:.2f} EUR")
            except Exception:
                pass
        _log(f"tender {tid} done: {len(boq)} rows, {n_csv} priced, total {total} EUR")
        _cancel_cb = None
        return {"ok": True, "pack": pack, "path": str(out)}
    except CancelledError:
        _cancel_cb = None
        _log(f"tender {tid} CANCELLED by operator")
        if run_id:
            try:
                costdb.run_close(dbp, run_id, "CANCELLED", st.get("kss", ""))
            except Exception:
                pass
        return {"ok": False, "cancelled": True, "error": "спряна от потребителя", "state": st}
    except Exception as ex:
        _cancel_cb = None
        st["error"] = str(ex)
        if run_id:
            try:
                costdb.run_close(dbp, run_id, "FAILED", str(ex)[:300])
            except Exception:
                pass
        try:
            (proc_dir / f"{tid}.error.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        return {"ok": False, "error": str(ex), "state": st}
