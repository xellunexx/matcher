# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Optional embedding veto for cost matching - the detachable semantic guard.

The deterministic scorer knows what the tokens say; this module knows what
the sentence *means*. It answers one question only: how close are two texts
in the multilingual embedding space the platform already maintains
(``app.core.vector`` + ``costs.vector_adapter``, e5 family by default).

Contract
--------
* **Attachable.** When the environment variable ``OE_COST_MATCH_SEMANTIC``
  is truthy the service asks this module to bless - or veto - the top
  suggestion of each line.
* **Veto-only.** A missing backend, a cold encoder or any failure returns
  ``None`` and the run behaves exactly as if the module were absent. High
  similarity can never *create* a match the lexical evidence did not find;
  low similarity can only demote a suggestion the rules already doubted.
* **Read-through explanations.** A veto is surfaced as the reason code
  ``low_semantic_similarity`` with the similarity in the factors, so the
  reviewer sees both the number and its consequence.

No I/O at import time; the encoder loads lazily on first use and failures
after that are memoised so a broken backend costs one log line per run, not
one per line.
"""

from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger(__name__)

#: Cosine floor for the e5-small space, calibrated against the golden
#: harness: true twins sit at >= 0.9, same-family paraphrases at 0.7-0.85,
#: cross-trade pairs below 0.6. Only *below* this value does the veto speak.
VETO_COSINE_FLOOR = 0.62

_encoder_state: str | None = None  # None=untried, "ok", "failed"


def enabled() -> bool:
    """Whether the semantic veto was requested for this process."""
    return os.environ.get("OE_COST_MATCH_SEMANTIC", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


async def cosine(text_a: str, text_b: str) -> float | None:
    """Cosine similarity in ``[-1, 1]``, or ``None`` when the backend is away.

    e5-class models are asymmetric: passages and queries are prefixed
    differently at encode time, matching the indexing convention in
    ``costs.vector_adapter``.
    """
    global _encoder_state
    if not enabled() or not text_a.strip() or not text_b.strip():
        return None
    if _encoder_state == "failed":
        return None
    try:
        # Pairwise similarity needs the *encoder* only - no vector store, no
        # database. Both imports below stay light on purpose: app.config for
        # the model name (prefix convention), app.core.vector for the encode.
        from app.config import get_settings  # noqa: PLC0415
        from app.core.vector import encode_texts_async  # noqa: PLC0415

        is_e5 = "e5" in (get_settings().embedding_model_name or "").lower()
        texts = [
            ("query: " + text_a) if is_e5 else text_a,
            ("passage: " + text_b) if is_e5 else text_b,
        ]
        vectors = await encode_texts_async(texts)
        _encoder_state = "ok"
    except Exception as exc:  # noqa: BLE001 - optional module, never a hard dep
        _encoder_state = "failed"
        logger.info("cost-match semantic veto disabled this run: %s", exc)
        return None
    if not vectors or len(vectors) < 2:
        return None
    a, b = vectors[0], vectors[1]
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return None
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


async def veto(query_text: str, candidate_text: str) -> float | None:
    """Return the similarity measured, when it says the pair is unrelated.

    ``None`` means "no signal" (inactive, unavailable, or similarity at or
    above the floor); a float below the floor means the two texts do not mean
    the same thing and the caller should demote the suggestion.
    """
    sim = await cosine(query_text, candidate_text)
    if sim is None or sim >= VETO_COSINE_FLOOR:
        return None
    return sim


__all__ = ["VETO_COSINE_FLOOR", "cosine", "enabled", "veto"]
