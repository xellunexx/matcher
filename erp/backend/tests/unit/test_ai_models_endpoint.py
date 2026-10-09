# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""``GET /ai/settings/models/{provider}`` - self-hosted model discovery.

The settings page asks what models the user's local runtime actually loaded
so the model field can be filled from the answer instead of typed blind.
What is pinned:

* only the self-hosted providers are probeable - a keyed provider's URL is a
  vendor endpoint, not the user's server;
* the stored chat-completions path is stripped back to the host root before
  ``/v1/models`` is appended;
* the response shape OpenAI-compatible runtimes return is parsed into a flat
  id list;
* an unreachable server reports a message instead of raising.

The handler is invoked directly with a stub settings service; ``httpx`` and
the SSRF guard are monkeypatched so no network happens.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import app.core.url_safety as url_safety
from app.modules.ai.router import list_self_hosted_models


def _service(**meta: Any) -> SimpleNamespace:
    settings = SimpleNamespace(metadata_=dict(meta))
    repo = SimpleNamespace(
        get_by_user_id=lambda _uid: _awaitable(settings),
    )
    return SimpleNamespace(settings_repo=repo)


async def _awaitable(value: Any) -> Any:
    return value


class _Response:
    def __init__(self, payload: Any):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


def _patch_http(monkeypatch: pytest.MonkeyPatch, payload: Any, captured: dict[str, str]) -> None:
    import httpx

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str, **_kw: Any) -> _Response:
            captured["url"] = url
            return _Response(payload)

    async def _no_guard(url: str, _allow: Any = None) -> str:
        return url

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())
    monkeypatch.setattr(url_safety, "resolve_and_validate_ai_provider_url", _no_guard)


USER_ID = "00000000-0000-0000-0000-000000000001"


def test_models_listed_from_saved_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    _patch_http(monkeypatch, {"data": [{"id": "gpt-oss-20b"}, {"id": "nope"}]}, captured)
    result = asyncio.run(
        list_self_hosted_models(
            "vllm",
            USER_ID,
            _service(vllm_base_url="http://127.0.0.1:10000"),
        )
    )
    assert result["models"] == ["gpt-oss-20b", "nope"]
    # The host root is probed at /v1/models, not the chat-completions path.
    assert captured["url"] == "http://127.0.0.1:10000/v1/models"


def test_stored_chat_path_is_stripped_back_to_root(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    _patch_http(monkeypatch, {"data": []}, captured)
    asyncio.run(
        list_self_hosted_models(
            "ollama",
            USER_ID,
            _service(ollama_base_url="http://gpu-box:11434/v1/chat/completions"),
        )
    )
    assert captured["url"] == "http://gpu-box:11434/v1/models"


def test_keyed_provider_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(list_self_hosted_models("openai", USER_ID, _service()))
    assert exc.value.status_code == 400


def test_no_saved_url_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    result = asyncio.run(list_self_hosted_models("vllm", USER_ID, _service()))
    assert result["models"] == []
    assert "URL" in result["message"]
