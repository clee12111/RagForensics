"""
Langfuse observability client for the RAG pipeline.

Wraps Langfuse 4.x OTel-based tracing. Each pipeline stage (embed, retrieve,
generate) calls langfuse_span() to emit a named child span under a parent trace.

Degrades gracefully when Langfuse is unavailable (keys missing or host
unreachable): logs a one-time warning and continues as a no-op so the graph
runs without tracing rather than crashing.

Usage in graph.py:
    from production_rag_forensics.observability.client import get_client, langfuse_span

    lf = get_client()
    with langfuse_span(lf, name="embed_query", obs_type="embedding", input={"query": q}):
        ...  # do work
        lf.update_current_span(output={"dim": 1536})
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager, nullcontext
from typing import Any

_log = logging.getLogger(__name__)

# Cached client — initialized once per process
_client: Any = None
_client_ok: bool | None = None   # None = not yet checked


def get_client() -> Any:
    """
    Return a live Langfuse client, or None if unavailable.

    Tries to initialize once; subsequent calls return the cached result.
    """
    global _client, _client_ok
    if _client_ok is not None:
        return _client

    public_key  = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key  = os.getenv("LANGFUSE_SECRET_KEY")
    host        = os.getenv("LANGFUSE_HOST", "http://localhost:3000")

    if not public_key or not secret_key:
        _log.warning(
            "Langfuse tracing DISABLED — LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY "
            "not set in environment. Start the Langfuse Docker stack and add keys to .env."
        )
        _client_ok = False
        _client = None
        return None

    try:
        from langfuse import Langfuse
        lf = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        # auth_check raises if the host is unreachable or keys are wrong
        lf.auth_check()
        _log.info("Langfuse tracing enabled — host=%s", host)
        _client_ok = True
        _client = lf
    except Exception as exc:
        _log.warning(
            "Langfuse tracing DISABLED — could not connect to %s: %s. "
            "Is the Docker stack running?", host, exc
        )
        _client_ok = False
        _client = None

    return _client


@contextmanager
def langfuse_span(
    client: Any,
    *,
    name: str,
    obs_type: str = "span",
    input: dict | None = None,
):
    """
    Context manager that opens a Langfuse observation span for the duration of
    the enclosed block, then closes it.

    When client is None (Langfuse unavailable), behaves as a no-op.

    Args:
        client:   Langfuse client from get_client(), or None.
        name:     Span name shown in the Langfuse UI.
        obs_type: Langfuse observation type — "span", "generation", "embedding",
                  "retriever", etc.
        input:    Dict logged as the span's input payload.
    """
    if client is None:
        yield
        return

    ctx = client.start_as_current_observation(
        name=name,
        as_type=obs_type,
        input=input or {},
    )
    with ctx:
        yield
