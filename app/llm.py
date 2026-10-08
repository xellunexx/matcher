# -*- coding: utf-8 -*-
"""LLM layer — OpenAI-compatible chat client (zero deps, stdlib urllib).
Works with: local llama-server (http://127.0.0.1:10000), OpenAI api, Azure-style, OpenRouter, etc.
"""
import json, time, urllib.request, urllib.error

def _req(url, payload=None, api_key=None, timeout=60, method=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=(method or ("POST" if data else "GET")))
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _norm_base(base_url):
    """Accept 'host', 'host/', 'host/v1' — anything OpenAI-compatible.
    If the caller omitted the version prefix, try as-is, then with /v1 appended."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ValueError("base_url is empty")
    return base


def _req_with_v1(path, payload=None, api_key=None, timeout=60, method=None, base_url=None):
    base = _norm_base(base_url)
    try:
        return base, _req(base + path, payload, api_key, timeout=timeout, method=method)
    except urllib.error.HTTPError as e:
        if e.code == 404 and not base.endswith("/v1"):
            base2 = base + "/v1"
            return base2, _req(base2 + path, payload, api_key, timeout=timeout, method=method)
        raise


def chat(base_url, model, messages, api_key=None, temperature=None, max_tokens=None, timeout=90, extra=None):
    base_url = _norm_base(base_url)
    payload = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra:
        payload.update(extra)
    for _ in range(3):
        try:
            _, r = _req_with_v1("/chat/completions", payload, api_key, timeout=timeout, base_url=base_url)
            return r
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            # Newer OpenAI-style models reject legacy params — retry translated.
            if e.code == 400 and "max_tokens" in body and "max_tokens" in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
                continue
            if e.code == 400 and "temperature" in body and "temperature" in payload:
                payload.pop("temperature")
                continue
            raise urllib.error.HTTPError(e.url, e.code, f"{e.reason} — {body[:300]}", e.headers, e.fp)
    raise RuntimeError("chat: retries exhausted")

def list_models(base_url, api_key=None, timeout=30):
    base_url = _norm_base(base_url)
    _, r = _req_with_v1("/models", None, api_key, timeout=timeout, method="GET", base_url=base_url)
    return r

def chat_text(base_url, model, messages, api_key=None, **kw):
    """Return just the assistant text."""
    r = chat(base_url, model, messages, api_key=api_key, **kw)
    return r["choices"][0]["message"]["content"]

def _model_ids(r):
    """All known ids: primary ids + llama-style aliases."""
    out = []
    for m in (r.get("data") or []):
        for cand in [m.get("id")] + list(m.get("aliases") or []):
            if cand and cand not in out:
                out.append(cand)
    return out

def _stem(p):
    """'C:\\ai\\models\\gpt-oss-20b-Q6_K.gguf' -> 'gpt-oss-20b-Q6_K' (tolerates / and \\ separators)."""
    base = p.replace("\\", "/").rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base

def match_model(want, ids):
    """Tolerant resolution. Returns (resolved_id, how) where how ∈ exact|case|name|sole|None.
    llama-server registers the full .gguf path as id, so a short name must still match."""
    if not ids:
        return None, None
    if want:
        if want in ids:
            return want, "exact"
        low = want.lower()
        for i in ids:
            if i.lower() == low:
                return i, "case"
        for i in ids:
            if _stem(i) == want or _stem(i).lower() == low or i.replace("\\", "/").rsplit("/", 1)[-1] == want:
                return i, "name"
    if len(ids) == 1:
        return ids[0], "sole"
    return None, None

def test_conn(base_url, model, api_key=None):
    """Quick connectivity + model presence probe. Returns (ok, note, resolved_model)."""
    t0 = time.time()
    try:
        r = list_models(base_url, api_key)
        elapsed = round(time.time() - t0, 2)
        ids = _model_ids(r)
        resolved, how = match_model(model, ids)
        if resolved and (how == "exact" or resolved == model):
            return True, f"OK ({elapsed}s); моделът „{resolved}“ е наличен (списък: {len(ids)}).", resolved
        if resolved:
            why = {"case": "различни главни/малки букви", "name": "сървърът го държи по файл-път",
                   "sole": "единствен модел на сървъра"}.get(how, how)
            return True, (f"OK ({elapsed}s); исканият „{model or '(празно)'}“ не е буквален id, но е разрешен към „{resolved}“ ({why}). "
                          f"Запази модела като „{resolved}“, за да няма двусмислица."), resolved
        avail = ", ".join(ids[:5]) + (" …" if len(ids) > 5 else "")
        return True, f"OK ({elapsed}s); списък модели: {len(ids)} ({avail}); исканият „{model}“ не е в списъка — провери.", None
    except Exception as e:
        return False, str(e), None
