"""Langfuse tracing for the AI routes (/agent and /reviews-rag).

Fail-open by design (same pattern as the singleagent PoV): without
LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY every function is a no-op, and a
Langfuse that is down or misconfigured disables tracing for the process instead
of breaking the turn. Pinned to the v2 SDK (``langfuse>=2,<3``): the v4 SDK
changes the API and fails silently.

Callers pass text that was ALREADY PII-masked (``llm_gateway.mask_pii``); the
trace is created only after the mask, never with the raw message.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("searchxvector.langfuse")

_client = None


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _get_client():
    global _client
    if not enabled():
        return None
    if _client is None:
        try:
            from langfuse import Langfuse
            candidate = Langfuse()
            # One auth_check per process: a Langfuse that is down must not leave
            # the demo with broken "trace" links; the feature disappears instead.
            if not candidate.auth_check():
                raise RuntimeError("Langfuse auth_check falhou")
            _client = candidate
        except Exception:  # noqa: BLE001 — tracing never breaks a turn
            logger.warning("Langfuse indisponível/mal configurado; tracing desligado neste processo")
            _client = False
    return _client or None


def start_trace(*, name: str, session_id: str | None, masked_input: str, metadata: dict | None = None):
    client = _get_client()
    if client is None:
        return None
    try:
        return client.trace(name=name, session_id=session_id, input=masked_input,
                            metadata=metadata or {})
    except Exception:  # noqa: BLE001
        logger.exception("falha ao criar trace Langfuse")
        return None


def log_span(trace, *, name: str, input_data=None, output_data=None, metadata: dict | None = None):
    if trace is None:
        return
    try:
        trace.span(name=name, input=input_data, output=output_data, metadata=metadata or {})
    except Exception:  # noqa: BLE001
        logger.exception("falha ao registrar span no Langfuse")


def log_generation(trace, *, name: str, model: str, usage: dict | None, metadata: dict | None = None):
    if trace is None:
        return
    try:
        trace.generation(name=name, model=model, metadata=metadata or {},
                         usage={"input": (usage or {}).get("input_tokens", 0),
                                "output": (usage or {}).get("output_tokens", 0),
                                "unit": "TOKENS"} if usage else None)
    except Exception:  # noqa: BLE001
        logger.exception("falha ao registrar generation no Langfuse")


def finish_trace(trace, *, masked_output: str | None = None, metadata: dict | None = None):
    if trace is None:
        return
    try:
        trace.update(output=masked_output, metadata=metadata or {})
    except Exception:  # noqa: BLE001
        logger.exception("falha ao finalizar trace Langfuse")
