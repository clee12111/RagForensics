"""
OpenAI embedder for FastAPI corpus chunks.

Turns Chunk objects from chunker.py into 1536-dim dense vectors using
text-embedding-3-small. Persists results to data/embeddings/chunks_embedded.jsonl
(gitignored). Does NOT touch Pinecone.

Design decisions (locked):
  - Model: text-embedding-3-small (1536 dims, $0.02/1M tokens)
  - Symmetric: same call for corpus and queries; no input_type parameter.
  - Batch size: 128 chunks per request. Comfortably within Tier 1 limits
    (1M TPM / 3000 RPM); no proactive throttling needed at corpus scale.
  - Backoff: tenacity exponential on openai.RateLimitError as a safety net.
    Will not fire at this corpus size, but correct practice for production code.
  - Token accounting: tiktoken cl100k_base (OpenAI's encoding for this model).
    Counts are exact for billing estimation; report total tokens + implied cost.
  - dimensions param: NOT passed -- default 1536 is used. Reduction available
    in the API but unused; full dims give maximum retrieval quality.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import tiktoken
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)
import logging

from production_rag_forensics.retrieval.chunker import Chunk, chunk_corpus

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "text-embedding-3-small"
EXPECTED_DIM = 1536
BATCH_SIZE = 128
# text-embedding-3-small uses cl100k_base
_ENC = tiktoken.get_encoding("cl100k_base")

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
CORPUS_DIR = _REPO_ROOT / "data" / "corpus"
EMBEDDINGS_DIR = _REPO_ROOT / "data" / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "chunks_embedded.jsonl"

# Cost: $0.02 / 1M tokens (text-embedding-3-small as of 2026)
_COST_PER_M_TOKENS = 0.02

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger(__name__)

# ── Retryable embed call ───────────────────────────────────────────────────────

@retry(
    retry=retry_if_exception_type(RateLimitError),
    wait=wait_random_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(6),
    before_sleep=before_sleep_log(_log, logging.WARNING),
    reraise=True,
)
def _embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Single batched embed call with exponential backoff on RateLimitError."""
    response = client.embeddings.create(input=texts, model=MODEL)
    # Response items come back in the same order as input
    return [item.embedding for item in response.data]


# ── Core embedding function ───────────────────────────────────────────────────

def embed_chunks(
    chunks: list[Chunk],
    client: OpenAI,
    *,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """
    Embed a list of Chunk objects in batches of BATCH_SIZE.

    Returns one record per chunk:
        {
            "chunk_id":    int,    # 0-based index; used as Pinecone vector ID
            "source_file": str,    # path relative to corpus root
            "header_path": str,
            "token_count": int,    # tiktoken cl100k_base (from chunker)
            "oversized":   bool,
            "text":        str,    # kept for retrieval display
            "vector":      list[float],  # 1536 floats
        }

    Raises ValueError immediately if any returned vector has dim != EXPECTED_DIM.
    """
    records: list[dict[str, Any]] = []
    total_tokens = 0
    total_requests = 0
    backoff_fired = False

    n = len(chunks)
    batches = [chunks[i : i + BATCH_SIZE] for i in range(0, n, BATCH_SIZE)]

    if verbose:
        print(f"Embedding {n} chunks in {len(batches)} batch(es) "
              f"(batch_size={BATCH_SIZE}, model={MODEL})")

    t_start = time.perf_counter()

    for batch_idx, batch in enumerate(batches):
        texts = [c.text for c in batch]

        # Count tokens before the call (exact: same encoding as OpenAI uses)
        batch_tokens = sum(len(_ENC.encode(t)) for t in texts)

        if verbose:
            print(f"  Batch {batch_idx + 1}/{len(batches)}: "
                  f"{len(texts)} chunks, {batch_tokens:,} tokens ...",
                  end=" ", flush=True)

        attempts_before = _embed_batch.statistics.get("attempt_number", 0)
        vectors = _embed_batch(client, texts)
        attempts_after = _embed_batch.statistics.get("attempt_number", 0)

        if attempts_after > attempts_before + 1:
            backoff_fired = True

        total_requests += 1
        total_tokens += batch_tokens

        for chunk, vec in zip(batch, vectors):
            dim = len(vec)
            if dim != EXPECTED_DIM:
                raise ValueError(
                    f"Dimension mismatch at chunk_id={len(records)}: "
                    f"got {dim}, expected {EXPECTED_DIM}"
                )

            try:
                rel = str(chunk.source_file.relative_to(CORPUS_DIR))
            except ValueError:
                rel = str(chunk.source_file)

            records.append({
                "chunk_id":    len(records),
                "source_file": rel,
                "header_path": chunk.header_path,
                "token_count": chunk.token_count,
                "oversized":   chunk.oversized,
                "text":        chunk.text,
                "vector":      vec,
            })

        if verbose:
            print(f"dim={len(vectors[0])} OK")

    elapsed = time.perf_counter() - t_start
    cost = total_tokens / 1_000_000 * _COST_PER_M_TOKENS

    if verbose:
        print(f"\n--- Embedding summary ---")
        print(f"Chunks embedded:      {len(records)}")
        print(f"All dims == {EXPECTED_DIM}:     "
              f"{'YES' if len(records) == n else 'NO -- see above'}")
        print(f"Tokens sent:          {total_tokens:,}  (tiktoken cl100k_base, exact)")
        print(f"Implied cost:         ${cost:.6f}  "
              f"(@ ${_COST_PER_M_TOKENS}/1M tokens for {MODEL})")
        print(f"Requests made:        {total_requests}")
        print(f"Wall-clock time:      {elapsed:.1f}s")
        print(f"429 backoff fired:    {'YES' if backoff_fired else 'no'}")

    return records


# ── Persistence ───────────────────────────────────────────────────────────────

def persist(records: list[dict[str, Any]], path: Path = EMBEDDINGS_FILE) -> None:
    """Write records to JSONL. One JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    size_mb = path.stat().st_size / 1_048_576
    print(f"Persisted {len(records)} records -> {path}  ({size_mb:.2f} MB)")


def load(path: Path = EMBEDDINGS_FILE) -> list[dict[str, Any]]:
    """Load records from JSONL written by persist()."""
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not found in environment -- check .env")
        sys.exit(1)
    print(f"OPENAI_API_KEY loaded ({len(api_key)} chars)")

    client = OpenAI(api_key=api_key)
    print(f"OpenAI client initialised  model={MODEL}  expected_dim={EXPECTED_DIM}\n")

    stage = sys.argv[1] if len(sys.argv) > 1 else "1"

    print(f"Loading chunks from corpus at {CORPUS_DIR} ...")
    all_chunks = chunk_corpus(CORPUS_DIR)
    print(f"Total chunks from chunker: {len(all_chunks)}\n")

    # ── Stage 1: first 20 chunks ──────────────────────────────────────────────
    if stage == "1":
        print("=" * 60)
        print("STAGE 1 -- embedding first 20 chunks (validation only)")
        print("=" * 60 + "\n")

        sample = all_chunks[:20]
        records = embed_chunks(sample, client, verbose=True)

        print(f"\n--- First vector sample ---")
        r0 = records[0]
        print(f"chunk_id=0  header='{r0['header_path'][:55]}'")
        print(f"  dim={len(r0['vector'])}")
        print(f"  first 5 floats: {[round(v, 6) for v in r0['vector'][:5]]}")

        print(f"\n--- Dimension check (all 20) ---")
        all_ok = True
        for rec in records:
            dim = len(rec["vector"])
            ok = dim == EXPECTED_DIM
            if not ok:
                all_ok = False
            print(f"  chunk_id={rec['chunk_id']:>3}  "
                  f"tokens={rec['token_count']:>4}  "
                  f"dim={dim}  {'OK' if ok else 'WRONG'}  "
                  f"{rec['header_path'][:50]}")

        print(f"\nAll 20 dims == {EXPECTED_DIM}: {'YES' if all_ok else 'NO -- STOP'}")
        print("\nSTAGE 1 COMPLETE -- confirm, then run with '2' for full corpus")

    # ── Stage 2: full corpus ──────────────────────────────────────────────────
    elif stage == "2":
        print("=" * 60)
        print("STAGE 2 -- embedding full corpus")
        print("=" * 60 + "\n")

        records = embed_chunks(all_chunks, client, verbose=True)

        print(f"\n--- Full dimension check ---")
        wrong = [r for r in records if len(r["vector"]) != EXPECTED_DIM]
        if wrong:
            print(f"  DIMENSION ERRORS: {len(wrong)} records -- STOP")
            for r in wrong:
                print(f"    chunk_id={r['chunk_id']}  dim={len(r['vector'])}")
            sys.exit(1)
        print(f"  ALL {len(records)} vectors dim={EXPECTED_DIM} -- PASS")

        persist(records)
        print(f"\nSTAGE 2 COMPLETE -- vectors ready for Pinecone indexer")

    else:
        print(f"Unknown stage '{stage}'. Use '1' or '2'.")
        sys.exit(1)
