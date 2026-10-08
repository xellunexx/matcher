# -*- coding: utf-8 -*-
"""ЦАИС ЕОП public acquisition adapter (app.eop.bg).
Only public JSON endpoints (no auth, no login, no captcha bypass).
Transport: bounded retries + backoff + jitter on transient failures (URLError/5xx/429);
4xx is terminal — never retried (a wrong id must fail fast).
"""
import json, random, re, time, urllib.error, urllib.request

SVC = "https://service.eop.bg/NX1Service.svc"
UA = {"Content-Type": "application/json; charset=utf-8",
      "User-Agent": "TenderOps/0.1 (public procurement research)"}

def _post(method, payload, timeout=90, tries=4, base_delay=0.4):
    data = json.dumps(payload).encode("utf-8")
    last = None
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(f"{SVC}/{method}", data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                raise  # терминално: погрешното ид/метод не се повтаря
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        if attempt < tries:
            time.sleep(min(8.0, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 0.25))
    raise last

def parse_tender_ref(s):
    """accept: 601701 | https://app.eop.bg/today/601701 | /today/601701"""
    s = (s or "").strip()
    m = re.search(r"/today/(\d+)", s)
    if m: return int(m.group(1))
    m = re.fullmatch(r"\d{5,}", s)
    if m: return int(s)
    return None

def ms_to_iso(ms):
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000, datetime.timezone.utc).astimezone(
            datetime.timezone(datetime.timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None

def fetch_tender(tid):
    """Pull the public picture of a tender. Returns a registry record."""
    det = _post("GetPublishedTenderDetails", {"tenderId": tid, "ianaTimeZone": "Europe/Sofia"})
    exports = _post("GetPublishedTenderExportsByTenderId", {"tenderId": tid, "ianaTimeZone": "Europe/Sofia"})
    anns = _post("GetPublicTenderAnnouncementsByTenderId", {"tenderId": tid, "ianaTimeZone": "Europe/Sofia"})
    dl = det.get("OfferPhaseEndDate", "")
    m = re.search(r"Date\((\d+)\)", dl)
    deadline = ms_to_iso(m.group(1)) if m else None
    docs = []
    for d in det.get("TenderDescriptionDocuments", []) or []:
        if d.get("IsFolder") or d.get("IsHidden"):
            continue
        docs.append({"name": d.get("Name"), "docId": d.get("Id"), "size": d.get("Size"),
                     "ext": d.get("Extension"), "modified": ms_to_iso(re.search(r"Date\((\d+)\)", d.get("ModifiedDate", "")).group(1)) if re.search(r"Date\((\d+)\)", d.get("ModifiedDate", "")) else None})
    return {
        "id": tid,
        "name": det.get("TenderName") or "",
        "buyer": det.get("OrganizationName") or "",
        "number": det.get("SpecialNumber") or "",
        "estValue": det.get("EstimatedValue"),
        "currency": "EUR",
        "deadline": deadline,
        "procedureType": det.get("ProcedureType"),
        "typeOfContract": det.get("TypeOfContract"),
        "url": f"https://app.eop.bg/today/{tid}",
        "acquiredAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "state": "acquired (public register level)",
        "documents": docs,
        "exports": [{"name": e.get("Name"), "docId": e.get("DocumentId"), "created": ms_to_iso(re.search(r"Date\((\d+)\)", e.get("CreatedDate", "")).group(1)) if re.search(r"Date\((\d+)\)", e.get("CreatedDate", "")) else None} for e in (exports or [])],
        "announcements": [{"id": a.get("Id"), "title": a.get("Title") or a.get("Text"), "created": ms_to_iso(re.search(r"Date\((\d+)\)", a.get("CreatedDate", "")).group(1)) if re.search(r"Date\((\d+)\)", a.get("CreatedDate", "")) else None} for a in (anns or [])],
        "descriptionHtml": re.sub(r"<[^>]+>", " ", det.get("TenderDescription") or "")[:2000],
    }

def signed_url(doc_id):
    return _post("GetSignedUrlByDocumentId", {"documentId": doc_id})
