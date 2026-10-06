"""llm_gateway.py — every LLM call of this PoV goes through the Grove gateway.

The agent (LangGraph ReAct) and the review RAG keep using LangChain's
``ChatAnthropic`` interface, but the HTTP call itself is delegated to
``grove_client.create_message`` from the shared package (``pov-shared``):

* destination and credentials validated by ``grove_client.client_settings()``
  (``GROVE_BASE_URL`` + ``GROVE_API_KEY``; ``Authorization: Bearer`` plus the
  real key in ``x-api-key``). There is deliberately NO fallback to
  ``ANTHROPIC_API_KEY`` or to a personal subscription;
* resilience on by default (retry with full-jitter backoff on 429/5xx/timeout,
  per-model circuit breaker, optional model fallback via
  ``GROVE_MODEL_FALLBACKS``). Tune or switch off with the ``GROVE_*`` env vars
  documented in ``_shared/README.md``.

When ``pov-shared`` is not installed (e.g. a public clone without access to the
private package) or the gateway is not configured, the LLM features answer
with a readable "unavailable" message; search, vector, hybrid, similar and
analytics keep working because they never touch an LLM.
"""

from __future__ import annotations

import logging
import os
import sys
from functools import lru_cache
from typing import Any

from langchain_anthropic import ChatAnthropic

logger = logging.getLogger("searchxvector.llm")

# The Grove gateway does not serve haiku; sonnet-5-5 is the workspace default.
DEFAULT_MODEL = "claude-sonnet-5-5"


class LLMUnavailable(RuntimeError):
    """The gateway is not configured or pov-shared is missing."""


def _import_grove():
    try:
        import grove_client  # installed via `uv pip install -e "../_shared[llm]"`
        return grove_client
    except ImportError:
        shared = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared"))
        if os.path.isfile(os.path.join(shared, "grove_client.py")) and shared not in sys.path:
            sys.path.insert(0, shared)
            try:
                import grove_client
                return grove_client
            except ImportError:
                pass
    return None


def gateway_status() -> dict:
    """Machine-readable view for /health/llm: never exposes host or key."""
    grove = _import_grove()
    if grove is None:
        return {"ok": False, "reason": "pov-shared (grove_client) não instalado"}
    try:
        cfg = grove.client_settings()
    except Exception as exc:  # noqa: BLE001 — config error, message has no secret
        return {"ok": False, "reason": str(exc)[:200]}
    return {"ok": bool(cfg.get("gateway")), "reason": None if cfg.get("gateway") else
            "GROVE_BASE_URL ausente: este PoV só chama LLM via gateway Grove"}


class GroveChatAnthropic(ChatAnthropic):
    """ChatAnthropic whose ``messages.create`` is ``grove_client.create_message``.

    LangChain builds the exact Anthropic payload (tools, cache_control, system);
    only the transport changes, so LangGraph's ReAct loop is untouched.
    """

    def _create(self, payload: dict) -> Any:  # noqa: D401 — LangChain hook
        grove = _import_grove()
        if grove is None:
            raise LLMUnavailable("pov-shared (grove_client) não instalado")
        if payload.get("stream"):
            # create_message closes its client after the call; streaming would
            # read from a closed connection. Nothing in this PoV streams.
            raise LLMUnavailable("streaming não suportado pelo gateway deste PoV")
        payload = dict(payload)
        payload.pop("betas", None)  # cache_control is GA; no beta endpoint needed
        model = payload.pop("model")
        return grove.create_message(model=model, **payload)

    async def _acreate(self, payload: dict) -> Any:
        import asyncio
        return await asyncio.to_thread(self._create, payload)


def build_chat_model(model_env: str, max_tokens: int) -> GroveChatAnthropic:
    """Build the LangChain model. Never raises at import time: a missing gateway
    surfaces on the first call, as a readable degraded answer."""
    grove = _import_grove()
    api_key, base_url = "unconfigured", None
    if grove is not None:
        try:
            cfg = grove.client_settings()
            if cfg.get("gateway"):
                api_key, base_url = cfg["api_key"], cfg["base_url"]
        except Exception:  # noqa: BLE001
            logger.warning("gateway Grove não configurado; recursos de IA ficam indisponíveis")
    model = os.getenv(model_env) or DEFAULT_MODEL
    return GroveChatAnthropic(
        model=model,
        # no `temperature`: claude-sonnet-5-5 rejects it (400 "deprecated for this model")
        max_tokens=max_tokens,
        api_key=api_key,
        anthropic_api_url=base_url,
        # retries live in grove_client (one policy, one breaker per process)
        max_retries=0,
    )


@lru_cache(maxsize=1)
def _mask_fn():
    grove = _import_grove()  # same sys.path fallback also exposes guardrails
    del grove
    try:
        from guardrails import mask_pii
        return lambda text: mask_pii(text).text
    except Exception:  # noqa: BLE001
        logger.warning("guardrails indisponível; máscara de PII local mínima em uso")
        import re
        email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
        cpf = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
        return lambda text: cpf.sub("<CPF>", email.sub("<EMAIL>", text))


def mask_pii(text: str) -> str:
    """PII mask applied BEFORE any trace is created (Langfuse)."""
    try:
        return _mask_fn()(text or "")
    except Exception:  # noqa: BLE001
        return "<texto omitido>"


# Minimal local fallback (used only when pov-shared is not installed, e.g. a
# public clone): the classic override / exfiltration phrasings in PT/EN/ES.
_FALLBACK_INJECTION = [
    r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules|prompt)",
    r"\b(ignore|ignora|esque[cç]a|desconsidere)\w*\b.{0,40}\b(instru[cç][oõ]es|regras|prompt)",
    r"\b(reveal|show|print|mostre|revele|exiba|muestra)\b.{0,40}\b(system prompt|prompt do sistema|instru[cç][oõ]es internas)",
    r"\b(you are now|agora voc[eê] [eé]|a partir de agora voc[eê])\b",
]


def check_injection(text: str) -> tuple[bool, str | None]:
    """Offline heuristic from pov-shared (PT/EN/ES). (flagged, reason)."""
    _import_grove()
    try:
        from guardrails import check_injection as _check
    except Exception:  # noqa: BLE001
        import re
        hit = any(re.search(p, text or "", re.I | re.S) for p in _FALLBACK_INJECTION)
        return hit, ("injection:fallback" if hit else None)
    try:
        res = _check(text or "")
    except Exception:  # noqa: BLE001
        return False, None
    return (not res.ok), res.reason
