# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""AI web-verify extraction client.

One call per *unique* bill line turns an LLM's market knowledge into a
structured ``WebEstimate`` record: family words, a price range with a
midpoint, a material/labour breakdown and a modifier->delta variant map.

The output is advisory. The endpoint generates from model knowledge - it is
not citing live pages - so records are honest ``ai_estimate`` leads for a
person to rule on, never written to a position by this code path. Applying
one is a ``manual`` decision through the normal ruling flow.

Strict JSON is requested and enforced: a prose answer that cannot be parsed
is a failed extraction, not a price to guess at.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Generous on purpose: the endpoint fronts a reasoning model whose thinking
# tokens burn through the budget before the JSON answer starts.
_MAX_TOKENS = 4000
_TIMEOUT_S = 240.0
# Cap on how much of the raw answer is persisted as provenance.
_RAW_KEEP = 4000
# Tool-loop bounds: enough for search -> page -> answer, not enough to spin.
_MAX_ROUNDS = 4
_MAX_SOURCES = 10
_PAGE_KEEP = 4000
_SEARCH_RESULTS = 5

_SYSTEM = (
    "You are a Bulgarian construction cost analyst. Estimate current Bulgarian "
    "market prices (materials + labour, EUR, VAT excluded) for bill-of-quantities "
    "lines. Answer ONLY with a single JSON object - no markdown, no prose."
)

_PROMPT = """Line: {description}
Unit: {unit}

Return exactly this JSON shape:
{{
  "eligible": true or false,
  "family_words": ["canonical head terms identifying this item family"],
  "price": {{"min": number, "max": number, "median": number, "currency": "EUR"}},
  "breakdown": [{{"component": "material"|"labor"|"consumables", "label": "...", "min": number, "max": number}}],
  "variants": [{{"modifier": ["tokens"], "price_delta": [lo, hi]}}],
  "confidence": "high"|"medium"|"low",
  "reason": "one sentence"
}}

Rules:
- eligible=false when the line is too project-specific to have a market price
  (e.g. site-specific totals, provisional sums, missing quantities). Then omit
  or null the price fields.
- price.min/max are the plausible market range per ONE unit for a standard
  execution; price.median is the midpoint of that range.
- variants capture spec modifiers that move the price (stainless vs brass,
  larger diameter, premium brand) as token lists + price deltas.
- When web_search/fetch_page tools are available, prefer figures you found on
  real supplier pages over memory; otherwise answer from knowledge.
- All money in EUR per unit."""

# Tool definitions handed to the endpoint. The model asks, this module does
# the fetching - the endpoint itself browses nothing.
_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web. Returns titles, URLs and snippets. "
                "Use it for supplier and marketplace price pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": (
                "Fetch one public page and return its text content "
                "(truncated). Use it to read a search result in full."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]


class WebVerifyError(RuntimeError):
    """The extraction call failed or returned something unparseable."""


def _number(value: Any) -> Decimal | None:
    """One parsed money field, or ``None`` when the model sent junk/null."""
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model reply.

    The model is told to answer with bare JSON, but a reasoning preamble or a
    stray fence happens; the first ``{``-to-matching-``}`` span is the answer.
    """
    start = text.find("{")
    if start < 0:
        raise WebVerifyError("model reply contained no JSON object")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    parsed = json.loads(candidate)
                except ValueError as exc:
                    raise WebVerifyError(f"model reply was not valid JSON: {exc}") from exc
                if isinstance(parsed, dict):
                    return parsed
                raise WebVerifyError("model reply JSON was not an object")
    raise WebVerifyError("model reply JSON object was unterminated")


def parse_estimate_payload(text: str) -> dict[str, Any]:
    """Turn a raw model reply into the stored estimate fields.

    Returns a dict with the ``WebEstimate`` columns filled. Raises
    ``WebVerifyError`` when the reply cannot be trusted as structured data -
    a hallucinated range in prose is not evidence.
    """
    data = _json_object(text)
    raw_price = data.get("price")
    price = raw_price if isinstance(raw_price, dict) else {}
    price_min = _number(price.get("min"))
    price_max = _number(price.get("max"))
    median = _number(price.get("median"))
    eligible = bool(data.get("eligible", True))
    if price_min is None or price_max is None:
        eligible = False
    if eligible and price_min is not None and price_max is not None:
        # The spec says midpoint; when the model forgot it, compute it - but
        # never let a supplied "median" sit outside its own range.
        if median is None or median < price_min or median > price_max:
            median = (price_min + price_max) / 2
    breakdown = data.get("breakdown")
    variants = data.get("variants")
    family = data.get("family_words")
    return {
        "eligible": eligible,
        "family_words": [str(w) for w in family] if isinstance(family, list) else [],
        "price_min": price_min,
        "price_max": price_max,
        "price_median": median if eligible else None,
        "currency": str(price.get("currency") or "EUR").strip().upper() or "EUR",
        "breakdown": [b for b in breakdown if isinstance(b, dict)] if isinstance(breakdown, list) else [],
        "variants": [v for v in variants if isinstance(v, dict)] if isinstance(variants, list) else [],
        "ai_confidence": str(data.get("confidence") or ""),
        "reason": str(data.get("reason") or "")[:1000],
        "raw": text[:_RAW_KEEP],
    }


# ── search backends the tool loop executes ────────────────────────────────

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
_TAG_RE = re.compile(r"<[^>]+>")
_DDG_RESULT_RE = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S
)
_DDG_SNIP_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html)).strip()


async def _search_brave(
    query: str, key: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    # NB: Brave's country enum has no "bg"; search_lang=bg is what actually
    # steers results to Bulgarian supplier pages.
    response = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": _SEARCH_RESULTS, "search_lang": "bg"},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise WebVerifyError(f"brave search answered HTTP {response.status_code}")
    hits = (response.json().get("web") or {}).get("results") or []
    return [
        {"title": h.get("title") or "", "url": h.get("url") or "",
         "snippet": h.get("description") or ""}
        for h in hits[:_SEARCH_RESULTS]
    ]


async def _search_reserp(
    query: str, key: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Reserp SERP API: POST a Google Search URL, get back result links."""
    google_url = (
        "https://www.google.com/search?"
        + urllib.parse.urlencode({"q": query, "gl": "bg", "hl": "bg"})
    )
    response = await client.post(
        "https://api.reserp.ai/v2/serp/search",
        json={"url": google_url},
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )
    if response.status_code != 200:
        raise WebVerifyError(f"reserp answered HTTP {response.status_code}")
    hits = (response.json() or {}).get("results") or []
    results: list[dict[str, Any]] = []
    for h in hits[:_SEARCH_RESULTS]:
        url = h.get("url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        text = str(h.get("text") or "")
        title, _, snippet = text.partition("\n")
        results.append(
            {"title": title.strip(), "url": url, "snippet": snippet.strip()}
        )
    return results


async def _search_duckduckgo(
    query: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Key-free fallback: the public html endpoint, parsed as text.

    Slower and coarser than a real API - fine at one call at a time, and it
    keeps the feature alive when no Brave key is configured.
    """
    response = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=_UA,
        timeout=30.0,
        follow_redirects=True,
    )
    if response.status_code != 200:
        raise WebVerifyError(f"duckduckgo answered HTTP {response.status_code}")
    html = response.text
    links = _DDG_RESULT_RE.findall(html)
    snippets = [_text(s) for s in _DDG_SNIP_RE.findall(html)]
    results: list[dict[str, Any]] = []
    for i, (href, title) in enumerate(links[:_SEARCH_RESULTS]):
        # DDG wraps targets as //duckduckgo.com/l/?uddg=<encoded>; unwrap it.
        parsed = urllib.parse.urlparse(href if "://" in href else f"https:{href}")
        real = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
        results.append(
            {
                "title": _text(title),
                "url": real,
                "snippet": snippets[i] if i < len(snippets) else "",
            }
        )
    return results


async def _run_search(
    query: str,
    provider: str,
    key: str,
    client: httpx.AsyncClient,
    reserp_key: str = "",
) -> list[dict[str, Any]]:
    """Run one search, falling through backends when the primary errors."""
    attempts: list[tuple[str, Any]] = []
    if provider == "brave" and key:
        attempts.append(("brave", lambda: _search_brave(query, key, client)))
    if provider == "reserp" and reserp_key:
        attempts.append(("reserp", lambda: _search_reserp(query, reserp_key, client)))
    if provider != "reserp" and reserp_key:
        attempts.append(("reserp", lambda: _search_reserp(query, reserp_key, client)))
    attempts.append(("duckduckgo", lambda: _search_duckduckgo(query, client)))
    last_exc: Exception | None = None
    for name, fn in attempts:
        try:
            return await fn()
        except (httpx.HTTPError, WebVerifyError) as exc:
            logger.warning("search backend %s failed: %s", name, exc)
            last_exc = exc
    raise WebVerifyError(f"all search backends failed: {last_exc}")


async def _run_fetch(url: str, client: httpx.AsyncClient) -> str:
    """One page's visible text, hard-capped - the model gets body, not DOM."""
    if not url.startswith(("http://", "https://")):
        raise WebVerifyError("fetch_page refused a non-http(s) url")
    try:
        response = await client.get(
            url, headers=_UA, timeout=20.0, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise WebVerifyError(f"page fetch failed: {exc}") from exc
    ctype = (response.headers.get("content-type") or "").lower()
    if "text" not in ctype and "json" not in ctype and "html" not in ctype:
        return f"[non-text content-type: {ctype or 'unknown'}]"
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", response.text)
    return _text(html)[:_PAGE_KEEP]


# ── Gemini with Google Search grounding ───────────────────────────────────
#
# One request carries the google_search tool; Google's index does the search
# inside the model call and the reply's groundingMetadata carries the pages
# it actually read - the API form of Google AI Mode.


_GEMINI_RESEARCH_PROMPT = (
    "Каква е актуалната пазарна цена в България за следната позиция от "
    "количествена сметка: «{description}» (единица: {unit})?\n\n"
    "Потърси реални цени от български доставчици и строителни сайтове. "
    "Посочи диапазона (мин–макс) за единица, разбивка материали/труд и "
    "кои варианти (материал, размер, марка) променят цената. "
    "Цени в EUR без ДДС ако е възможно, иначе в лв."
)


async def _gemini_call(
    client: httpx.AsyncClient,
    endpoint: str,
    api_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """One generateContent round-trip -> the first candidate dict."""
    try:
        response = await client.post(
            endpoint,
            headers={"x-goog-api-key": api_key},
            json=payload,
            timeout=_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        raise WebVerifyError(f"gemini endpoint unreachable: {exc}") from exc
    if response.status_code != 200:
        raise WebVerifyError(
            f"gemini answered HTTP {response.status_code}: {response.text[:200]}"
        )
    try:
        body = response.json()
        return body["candidates"][0]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise WebVerifyError(
            f"gemini returned an unexpected body: {exc}"
        ) from exc


def _candidate_text(candidate: dict[str, Any]) -> str:
    parts = (candidate.get("content") or {}).get("parts") or []
    return "".join(
        p.get("text", "") for p in parts if isinstance(p, dict)
    )


async def _fetch_estimate_gemini(
    description: str,
    unit: str,
    *,
    api_key: str,
    model: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """AI-Mode-style extraction: a grounded search answer, then structured JSON.

    Two calls on purpose. The first asks the market question in prose with
    ``google_search`` grounding - Google runs real queries and returns the
    synthesized answer plus the pages it read. The second (ungrounded) call
    turns that answer into the strict JSON estimate shape. A single
    strict-JSON call rarely triggers grounding, so splitting keeps the
    evidence real instead of falling back to bare model memory.
    """
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )

    async def _call(c: httpx.AsyncClient) -> dict[str, Any]:
        # Step 1: grounded research - the AI Mode answer with citations.
        research = await _gemini_call(
            c,
            endpoint,
            api_key,
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": _GEMINI_RESEARCH_PROMPT.format(
                                    description=description,
                                    unit=unit or "-",
                                )
                            }
                        ],
                    }
                ],
                "tools": [{"google_search": {}}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": _MAX_TOKENS,
                },
            },
        )
        research_text = _candidate_text(research)
        grounding = research.get("groundingMetadata") or {}
        sources: list[dict[str, Any]] = []
        for chunk in grounding.get("groundingChunks") or []:
            web = chunk.get("web") if isinstance(chunk, dict) else None
            if web and web.get("uri"):
                sources.append(
                    {
                        "title": str(web.get("title") or ""),
                        "url": str(web.get("uri") or ""),
                        "snippet": "",
                    }
                )
        del sources[_MAX_SOURCES:]

        # Step 2: structure the grounded answer into the estimate shape.
        structure_prompt = _PROMPT.format(
            description=description, unit=unit or "-"
        )
        if research_text.strip():
            structure_prompt += (
                "\n\nMarket research gathered from web search "
                "(base your figures on this, not on memory):\n"
                + research_text[:6000]
            )
        candidate = await _gemini_call(
            c,
            endpoint,
            api_key,
            {
                "system_instruction": {"parts": [{"text": _SYSTEM}]},
                "contents": [
                    {"role": "user", "parts": [{"text": structure_prompt}]}
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "maxOutputTokens": _MAX_TOKENS,
                },
            },
        )
        text = _candidate_text(candidate)
        if not text.strip():
            raise WebVerifyError("gemini returned empty content")
        fields = parse_estimate_payload(text)
        fields["sources"] = sources
        queries = grounding.get("webSearchQueries") or []
        provenance = ""
        if queries:
            provenance += (
                "webSearchQueries: "
                + json.dumps(queries, ensure_ascii=False)
                + "\n\n"
            )
        if research_text.strip():
            provenance += "grounded answer:\n" + research_text[:2000] + "\n\n"
        if provenance:
            fields["raw"] = (provenance + str(fields.get("raw") or ""))[:_RAW_KEEP]
        return fields

    if client is not None:
        return await _call(client)
    async with httpx.AsyncClient() as c:
        return await _call(c)


async def fetch_estimate(
    description: str,
    unit: str,
    *,
    url: str,
    api_key: str,
    model: str,
    client: httpx.AsyncClient | None = None,
    search_provider: str = "",
    search_key: str = "",
    provider: str = "openai",
    gemini_model: str = "gemini-2.5-flash",
    reserp_key: str = "",
    fallback_lock: asyncio.Semaphore | None = None,
    openai_key: str | None = None,
) -> dict[str, Any]:
    """One extraction for one line - a tool loop, not a single call.

    The endpoint can emit ``web_search``/``fetch_page`` tool calls; this loop
    executes them against the configured search backend and feeds the results
    back until the model answers with the JSON estimate or the round cap
    hits. ``client`` is injectable so a batch run reuses one connection pool
    and a test can hand in a stub. Raises ``WebVerifyError`` on transport
    and parse failures alike - the caller records the failure and moves on.
    """
    if provider == "gemini":
        # Grounded generation: Google Search runs inside the model call.
        try:
            return await _fetch_estimate_gemini(
                description,
                unit,
                api_key=api_key,
                model=gemini_model,
                client=client,
            )
        except WebVerifyError as exc:
            # Quota exhausted / endpoint down is a line-level failure unless
            # the OpenAI-compatible endpoint is also configured - then it is
            # the fallback model, run serially under the shared gate.
            if not (url and model):
                raise
            logger.warning(
                "gemini extraction failed (%s); falling back to %s", exc, model
            )
    # The fallback signs with the OpenAI-compatible endpoint's own key - the
    # Gemini key that came in as ``api_key`` must never reach that server.
    headers = {
        "Authorization": f"Bearer {openai_key or api_key}",
        "Content-Type": "application/json",
    }
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _PROMPT.format(description=description, unit=unit or "-")},
    ]
    sources: list[dict[str, Any]] = []
    tools_on = bool(search_provider or reserp_key)

    async def _run(c: httpx.AsyncClient) -> dict[str, Any]:
        # The NVL72 endpoint is a shared queue: even when several Gemini
        # calls run in parallel, a kimi fallback chain stays one-at-a-time.
        gate = (
            fallback_lock
            if fallback_lock is not None
            else contextlib.nullcontext()
        )
        async with gate:
            return await _run_loop(c)

    async def _run_loop(c: httpx.AsyncClient) -> dict[str, Any]:
        for _ in range(_MAX_ROUNDS):
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": _MAX_TOKENS,
                "temperature": 0.1,
            }
            if tools_on:
                payload["tools"] = _TOOLS
                payload["tool_choice"] = "auto"
            try:
                response = await c.post(
                    url, json=payload, headers=headers, timeout=_TIMEOUT_S
                )
            except httpx.HTTPError as exc:
                raise WebVerifyError(f"verify endpoint unreachable: {exc}") from exc
            if response.status_code != 200:
                raise WebVerifyError(
                    f"verify endpoint answered HTTP {response.status_code}"
                )
            try:
                body = response.json()
                choice = body["choices"][0]
                message = choice["message"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise WebVerifyError(
                    f"verify endpoint returned an unexpected body: {exc}"
                ) from exc

            calls = message.get("tool_calls") or []
            if not calls:
                content = message.get("content")
                if not content:
                    raise WebVerifyError("verify endpoint returned empty content")
                fields = parse_estimate_payload(str(content))
                fields["sources"] = sources
                return fields

            # The model asked for tools: run each call, answer as tool
            # messages, and loop for the next turn.
            messages.append(message)
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                try:
                    if name == "web_search":
                        hits = await _run_search(
                            str(args.get("query") or ""),
                            search_provider,
                            search_key,
                            c,
                            reserp_key=reserp_key,
                        )
                        tool_content = json.dumps(hits, ensure_ascii=False)
                        sources.extend(hits)
                    elif name == "fetch_page":
                        page_url = str(args.get("url") or "")
                        page_text = await _run_fetch(page_url, c)
                        tool_content = page_text
                        sources.append({"title": "", "url": page_url, "snippet": ""})
                    else:
                        tool_content = f"[unknown tool: {name}]"
                except WebVerifyError as exc:
                    # A failed fetch is data too - tell the model, let it
                    # try another result instead of sinking the line.
                    tool_content = f"[{name} failed: {exc}]"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": tool_content[:_RAW_KEEP],
                    }
                )
                del sources[_MAX_SOURCES:]

        # The model spent its search budget without answering. Force one
        # closing turn with no tools on the table: it must commit to the
        # JSON estimate (possibly eligible=false) or the line honestly fails.
        messages.append(
            {
                "role": "user",
                "content": (
                    "Stop searching. Answer NOW with the single JSON object "
                    "the first user message specified - no markdown, no prose. "
                    "If the evidence you gathered is insufficient, return the "
                    "JSON with eligible=false."
                ),
            }
        )
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": _MAX_TOKENS,
            "temperature": 0.1,
        }
        try:
            response = await c.post(
                url, json=payload, headers=headers, timeout=_TIMEOUT_S
            )
        except httpx.HTTPError as exc:
            raise WebVerifyError(f"verify endpoint unreachable: {exc}") from exc
        if response.status_code != 200:
            raise WebVerifyError(
                f"verify endpoint answered HTTP {response.status_code}"
            )
        try:
            content = response.json()["choices"][0]["message"].get("content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise WebVerifyError(
                f"verify endpoint returned an unexpected body: {exc}"
            ) from exc
        if not content:
            raise WebVerifyError("verify endpoint returned empty content")
        fields = parse_estimate_payload(str(content))
        fields["sources"] = sources
        return fields

    if client is not None:
        return await _run(client)
    async with httpx.AsyncClient() as c:
        return await _run(c)


def estimate_signature(description: str, unit: str) -> str:
    """The dedupe key one unique line gets, shared across runs and projects.

    A hash, not the text itself: the signature must fit its column no matter
    how long a bill's descriptions get, and two lines that differ only past
    character 500 are still different lines.
    """
    from app.modules.cost_match.matcher import normalize_text

    norm = normalize_text(description)
    norm_unit = re.sub(r"\s+", "", (unit or "").lower())
    return hashlib.sha256(f"{norm}|{norm_unit}".encode()).hexdigest()
