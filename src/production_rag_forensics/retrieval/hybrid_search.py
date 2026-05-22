"""
Hybrid retrieval: BM25 sparse + Pinecone dense, merged via Reciprocal Rank Fusion.

Interface:
    get_hybrid_search(index) -> HybridSearch  # singleton per process
    HybridSearch.search(query, query_embedding, top_k, dense_n, sparse_n)

Each returned chunk carries:
    score        — alias for rrf_score (harness-compatible primary key)
    rrf_score    — fused RRF score (sum of 1/(k+rank) across both lists)
    dense_rank   — rank in Pinecone result (None if chunk was BM25-only)
    sparse_rank  — rank in BM25 result (None if chunk was dense-only)
    dense_score  — original Pinecone cosine score (0.0 if BM25-only)

RRF constant k=60 (standard; softens sensitivity to exact rank at the top).

Tokenization: lowercase [a-z0-9]+ tokens. Keeps identifiers like
"oauth2passwordbearer" or "tokenurl" as single queryable tokens — the exact
keyword-match mechanism we need to rescue FM-1 retrieval misses.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Path relative to repo root (where the process runs from)
_CHUNKS_PATH = Path(__file__).parents[3] / "data" / "embeddings" / "chunks_embedded.jsonl"

_RRF_K    = 60   # standard RRF constant
_DENSE_N  = 20   # default Pinecone candidate pool
_SPARSE_N = 20   # default BM25 candidate pool


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens. Splits CamelCase by boundary, keeps numbers."""
    return re.findall(r"[a-z0-9]+", text.lower())


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: "HybridSearch | None" = None


def get_hybrid_search(index) -> "HybridSearch":
    """
    Return the process-wide HybridSearch, building the BM25 index on first call.

    Args:
        index: Live Pinecone index object from graph.py.
    """
    global _instance
    if _instance is None:
        _instance = HybridSearch(index)
    return _instance


# ── HybridSearch class ────────────────────────────────────────────────────────

class HybridSearch:
    """BM25 + dense retrieval fused with RRF."""

    def __init__(self, index) -> None:
        from rank_bm25 import BM25Okapi  # type: ignore

        self._index = index

        # Load corpus chunks (text + metadata, no vectors needed)
        chunks: list[dict] = []
        with _CHUNKS_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                chunks.append({
                    "chunk_id":   rec["chunk_id"],
                    "source_file": rec["source_file"],
                    "header_path": rec["header_path"],
                    "text":        rec["text"],
                })

        self._chunks = chunks
        self._id_to_idx: dict[str, int] = {
            c["chunk_id"]: i for i, c in enumerate(chunks)
        }

        corpus_tokens = [_tokenize(c["text"]) for c in chunks]
        self._bm25 = BM25Okapi(corpus_tokens)

        print(
            f"HybridSearch: BM25 index built over {len(chunks)} chunks "
            f"(path: {_CHUNKS_PATH})",
            flush=True,
        )

    def search(
        self,
        query: str,
        query_embedding: list[float],
        top_k: int = 5,
        dense_n: int = _DENSE_N,
        sparse_n: int = _SPARSE_N,
    ) -> list[dict]:
        """
        Retrieve top_k chunks via RRF fusion of dense and sparse results.

        Args:
            query:          Raw query string (used for BM25 tokenization).
            query_embedding: Dense embedding vector (used for Pinecone query).
            top_k:          Number of final fused results to return.
            dense_n:        Candidate pool size for Pinecone dense retrieval.
            sparse_n:       Candidate pool size for BM25 sparse retrieval.

        Returns:
            List of chunk dicts sorted by rrf_score descending.
        """
        # ── Dense: Pinecone ───────────────────────────────────────────────────
        dense_result = self._index.query(
            vector=query_embedding, top_k=dense_n, include_metadata=True
        )

        dense_by_id: dict[str, dict] = {}
        dense_ranked: list[str] = []

        for match in dense_result.matches:
            cid = match.id
            dense_by_id[cid] = {
                "chunk_id":    cid,
                "text":        match.metadata.get("text", ""),
                "source_file": match.metadata.get("source_file", ""),
                "header_path": match.metadata.get("header_path", ""),
                "dense_score": match.score,
            }
            dense_ranked.append(cid)

        # ── Sparse: BM25 ──────────────────────────────────────────────────────
        tokens = _tokenize(query)
        bm25_scores = self._bm25.get_scores(tokens)

        top_sparse_indices = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True,
        )[:sparse_n]

        sparse_ranked: list[str] = [
            self._chunks[i]["chunk_id"] for i in top_sparse_indices
        ]

        # ── RRF merge ─────────────────────────────────────────────────────────
        rrf: dict[str, float] = {}
        d_rank: dict[str, int] = {}
        s_rank: dict[str, int] = {}

        for rank, cid in enumerate(dense_ranked, start=1):
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
            d_rank[cid] = rank

        for rank, cid in enumerate(sparse_ranked, start=1):
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
            s_rank[cid] = rank

        fused = sorted(rrf.items(), key=lambda x: x[1], reverse=True)[:top_k]

        # ── Build result ──────────────────────────────────────────────────────
        results: list[dict] = []
        for cid, rrf_score in fused:
            if cid in dense_by_id:
                base = dict(dense_by_id[cid])
            else:
                # BM25-only: look up from local store
                idx = self._id_to_idx.get(cid)
                if idx is None:
                    continue
                c = self._chunks[idx]
                base = {
                    "chunk_id":    cid,
                    "text":        c["text"],
                    "source_file": c["source_file"],
                    "header_path": c["header_path"],
                    "dense_score": 0.0,
                }

            base["rrf_score"]   = round(rrf_score, 6)
            base["score"]       = round(rrf_score, 6)   # harness-compatible alias
            base["dense_rank"]  = d_rank.get(cid)       # None if BM25-only
            base["sparse_rank"] = s_rank.get(cid)       # None if dense-only
            results.append(base)

        return results
