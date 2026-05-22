"""
Cross-encoder reranker using sentence-transformers.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2
    ~80MB, downloads and caches on first run.
    Trained on MS MARCO passage ranking — strong general-purpose retrieval signal.

Key difference from the Haiku reranker: all N candidates are scored in ONE
batched forward pass via CrossEncoder.predict(). No per-chunk API call, no
per-chunk latency, no API cost.

Usage:
    from production_rag_forensics.retrieval.cross_encoder_reranker import get_reranker

    reranked = get_reranker().rerank(query, chunks, top_k=5)
    # reranked[0]["cross_encoder_score"]  — raw logit score (higher = more relevant)
    # reranked[0]["dense_score"]          — original Pinecone cosine score

The module-level singleton (get_reranker) loads the model exactly once per
process. Calling get_reranker() on subsequent queries returns the cached instance.
"""

from __future__ import annotations

MAX_PER_SOURCE = 2  # max chunks from the same source_file in the returned top_k

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ── Module-level singleton ────────────────────────────────────────────────────

_instance: "CrossEncoderReranker | None" = None


def get_reranker() -> "CrossEncoderReranker":
    """Return the process-wide CrossEncoderReranker, loading the model on first call."""
    global _instance
    if _instance is None:
        print("Loading cross-encoder model (first call)...", flush=True)
        _instance = CrossEncoderReranker()
    return _instance


# ── Reranker class ────────────────────────────────────────────────────────────

class CrossEncoderReranker:
    def __init__(self) -> None:
        from sentence_transformers import CrossEncoder  # type: ignore
        self._model = CrossEncoder(_MODEL_NAME)

    def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_k: int = 5,
        max_per_source: int = MAX_PER_SOURCE,
    ) -> list[dict]:
        """
        Score all candidate chunks in one batched forward pass and return the
        top_k highest-scoring chunks with a per-source diversity cap applied.

        Each returned chunk gets:
            cross_encoder_score — raw logit from the cross-encoder (higher = better)
            dense_score         — original Pinecone cosine score (copied from "score")
        """
        if not chunks:
            return []

        pairs = [[query, c.get("text", "")] for c in chunks]
        scores = self._model.predict(pairs)

        scored: list[dict] = []
        for chunk, score in zip(chunks, scores):
            scored.append({
                **chunk,
                "dense_score":         chunk.get("score", 0.0),
                "cross_encoder_score": float(score),
            })

        # Sort by cross_encoder_score descending
        scored.sort(key=lambda c: c["cross_encoder_score"], reverse=True)

        # Apply per-source diversity cap
        source_counts: dict[str, int] = {}
        result: list[dict] = []
        overflow: list[dict] = []

        for c in scored:
            src = c.get("source_file", "")
            if source_counts.get(src, 0) < max_per_source:
                result.append(c)
                source_counts[src] = source_counts.get(src, 0) + 1
                if len(result) == top_k:
                    break
            else:
                overflow.append(c)

        # Fill up to top_k if cap left us short (rare)
        if len(result) < top_k:
            for c in overflow:
                result.append(c)
                if len(result) == top_k:
                    break

        return result
