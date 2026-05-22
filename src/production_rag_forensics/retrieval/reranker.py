"""
Haiku 4.5 reranker for RAG retrieval.

Takes a query and up to 20 candidate chunks, scores each chunk with a single
Haiku API call, and returns the top_k highest-scoring chunks in ranked order.

Usage:
    from production_rag_forensics.retrieval.reranker import Reranker

    reranker = Reranker()
    reranked = reranker.rerank(query, chunks, top_k=5)
    # reranked[0]["reranker_score"]  — Haiku relevance score 0.0-1.0
    # reranked[0]["dense_score"]     — original Pinecone cosine score
    # reranked[0]["rerank_cost_usd"] — total cost for this rerank call (first chunk only)

Pricing: Haiku 4.5 — $0.80/$4.00 per million input/output tokens
"""

from __future__ import annotations

import json
import os
import time

import anthropic
from anthropic import Anthropic

_529_WAITS = [15, 30, 60]  # seconds; fail loud after 3 retries

# ── Pricing ───────────────────────────────────────────────────────────────────

_INPUT_PER_M  = 0.80   # $/M input tokens
_OUTPUT_PER_M = 4.00   # $/M output tokens

_MODEL = "claude-haiku-4-5-20251001"

# ── Prompts ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a relevance scoring assistant. Score how well the provided "
    "context chunk answers the given question. Return only a JSON object."
)

_USER_TEMPLATE = """\
Question: {query}

Chunk:
{chunk_text}

Score the relevance of this chunk to the question on a scale of 0.0 to 1.0:
- 1.0: chunk directly and completely answers the question
- 0.7-0.9: chunk is highly relevant, covers most of what's needed
- 0.4-0.6: chunk is partially relevant, covers some aspect of the question
- 0.1-0.3: chunk is tangentially related but not directly useful
- 0.0: chunk is not relevant to the question

Respond in this exact JSON format:
{{"relevance": <float 0.0-1.0>, "reason": "<one phrase>"}}\
"""


def _compute_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * _INPUT_PER_M + output_tokens * _OUTPUT_PER_M) / 1_000_000


def _parse_response(raw: str) -> tuple[float, str]:
    """
    Parse JSON from Haiku relevance response.
    Raises ValueError with raw text on parse failure.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(l for l in lines if not l.startswith("```")).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Reranker returned non-JSON: {raw!r}") from exc

    relevance = data.get("relevance")
    reason = data.get("reason", "")

    if not isinstance(relevance, (int, float)) or not (0.0 <= float(relevance) <= 1.0):
        raise ValueError(f"Invalid relevance value {relevance!r} in: {raw!r}")
    if not isinstance(reason, str):
        raise ValueError(f"Invalid reason value {reason!r} in: {raw!r}")

    return float(relevance), reason


# ── Reranker ──────────────────────────────────────────────────────────────────

class Reranker:
    def __init__(self) -> None:
        self._client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _call_with_retry(self, user_msg: str):
        """Call Haiku with 529-retry backoff. All other errors propagate."""
        for attempt, wait in enumerate(_529_WAITS, start=1):
            try:
                return self._client.messages.create(
                    model=_MODEL,
                    max_tokens=60,
                    temperature=0,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
            except anthropic.APIStatusError as exc:
                if exc.status_code != 529:
                    raise
                print(f"  529 overloaded (reranker) -- retry {attempt}/3 in {wait}s")
                time.sleep(wait)
        # Final attempt — let any exception propagate
        return self._client.messages.create(
            model=_MODEL,
            max_tokens=60,
            temperature=0,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

    def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 5,
    ) -> list[dict]:
        """
        Score each chunk against the query and return the top_k highest-scoring
        chunks in descending reranker_score order.

        Each returned chunk gets:
            reranker_score  — float 0.0-1.0 from Haiku
            reranker_reason — one-phrase explanation from Haiku
            dense_score     — original Pinecone cosine score (copied from "score")

        The first chunk in the returned list also gets:
            rerank_cost_usd — total Haiku cost for this rerank call

        The original "score" field is left intact.
        """
        if not chunks:
            return []

        total_cost = 0.0
        scored: list[dict] = []

        for chunk in chunks:
            user_msg = _USER_TEMPLATE.format(
                query=query,
                chunk_text=chunk.get("text", ""),
            )
            response = self._call_with_retry(user_msg)

            raw = response.content[0].text
            relevance, reason = _parse_response(raw)

            inp = response.usage.input_tokens
            out = response.usage.output_tokens
            total_cost += _compute_cost(inp, out)

            scored_chunk = {
                **chunk,
                "dense_score":      chunk.get("score", 0.0),
                "reranker_score":   relevance,
                "reranker_reason":  reason,
            }
            scored.append(scored_chunk)

        # Sort by reranker_score descending, take top_k
        scored.sort(key=lambda c: c["reranker_score"], reverse=True)
        result = scored[:top_k]

        # Attach total rerank cost to first chunk
        if result:
            result[0]["rerank_cost_usd"] = round(total_cost, 6)

        return result
