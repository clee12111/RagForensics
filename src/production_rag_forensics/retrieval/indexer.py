"""
Pinecone indexer for the FastAPI corpus.

Reads data/embeddings/chunks_embedded.jsonl (584 records, 1536-dim vectors +
chunk text + metadata) and upserts into a Pinecone serverless index.
No re-embedding of the corpus — vectors come from the persisted embedder output.

Design decisions (locked):
  - Index name:  fastapi-docs-v1
  - Dimension:   1536  (text-embedding-3-small default output)
  - Metric:      cosine
  - Spec:        ServerlessSpec(cloud="aws", region="us-east-1")  [free tier]
  - Upsert batch size: 100 vectors (within Pinecone's recommended range)
  - Metadata stored per vector: source_file, header_path, token_count,
    oversized, text.  Largest text is ~7 KB; Pinecone's ~40 KB per-vector
    metadata limit is never approached.
  - Uses pc.indexes.create() — the current API, not the deprecated
    pc.create_index() shim confirmed in smoke test.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec

# ── Constants ─────────────────────────────────────────────────────────────────

INDEX_NAME     = "fastapi-docs-v1"
DIMENSION      = 1536
METRIC         = "cosine"
CLOUD          = "aws"
REGION         = "us-east-1"
UPSERT_BATCH   = 100

EMBED_MODEL    = "text-embedding-3-small"
METADATA_LIMIT = 40_960   # bytes; Pinecone per-vector metadata hard limit

_REPO_ROOT      = Path(__file__).parent.parent.parent.parent
EMBEDDINGS_FILE = _REPO_ROOT / "data" / "embeddings" / "chunks_embedded.jsonl"


# ── Index management ──────────────────────────────────────────────────────────

def get_or_create_index(pc: Pinecone) -> Any:
    """
    Return a handle to INDEX_NAME, creating it if it does not exist.

    If the index already exists, reports its current status and skips creation.
    Polls until the index status is ready before returning.
    """
    existing = [idx.name for idx in pc.list_indexes()]

    if INDEX_NAME in existing:
        print(f"Index '{INDEX_NAME}' already exists — skipping creation.")
    else:
        print(f"Creating index '{INDEX_NAME}' "
              f"(dim={DIMENSION}, metric={METRIC}, "
              f"cloud={CLOUD}, region={REGION}) ...")
        pc.indexes.create(
            name=INDEX_NAME,
            dimension=DIMENSION,
            metric=METRIC,
            spec=ServerlessSpec(cloud=CLOUD, region=REGION),
        )
        print("  Create request accepted. Polling for ready status ...")

    # Poll until ready
    for attempt in range(60):
        desc = pc.describe_index(INDEX_NAME)
        status = desc.status.get("ready", False) if isinstance(desc.status, dict) else getattr(desc.status, "ready", False)
        if status:
            break
        print(f"  [{attempt + 1}/60] status={desc.status} — waiting 5s ...")
        time.sleep(5)
    else:
        raise RuntimeError(f"Index '{INDEX_NAME}' did not become ready within 5 minutes")

    print(f"\nIndex ready. Description:")
    print(f"  name:      {desc.name}")
    print(f"  dimension: {desc.dimension}")
    print(f"  metric:    {desc.metric}")
    print(f"  status:    {desc.status}")
    return pc.Index(INDEX_NAME)


# ── Upsert ────────────────────────────────────────────────────────────────────

def upsert_vectors(index: Any, records: list[dict]) -> None:
    """
    Build Pinecone vectors from embedder records and upsert in batches.

    Each vector:
        id:     str(chunk_id)
        values: 1536-dim float list
        metadata: source_file, header_path, token_count, oversized, text

    Flags any record whose text field exceeds METADATA_LIMIT bytes before
    upserting (does NOT truncate — reports and exits if any are found).
    """
    # Pre-flight metadata size check
    oversized_meta: list[tuple[int, int]] = []
    for rec in records:
        text_bytes = len(rec["text"].encode("utf-8"))
        if text_bytes > METADATA_LIMIT:
            oversized_meta.append((rec["chunk_id"], text_bytes))

    if oversized_meta:
        print(f"METADATA SIZE VIOLATION — {len(oversized_meta)} records exceed "
              f"{METADATA_LIMIT:,} bytes:")
        for cid, nb in oversized_meta:
            print(f"  chunk_id={cid}  {nb:,} bytes")
        print("Aborting upsert. Reduce text size or metadata before retrying.")
        sys.exit(1)
    else:
        print(f"Metadata size check: all {len(records)} records under "
              f"{METADATA_LIMIT:,} bytes -- OK")

    n = len(records)
    batches = [records[i : i + UPSERT_BATCH] for i in range(0, n, UPSERT_BATCH)]
    total_upserted = 0

    print(f"Upserting {n} vectors in {len(batches)} batch(es) "
          f"(batch_size={UPSERT_BATCH}) ...")

    for batch_idx, batch in enumerate(batches):
        vectors = [
            {
                "id": str(rec["chunk_id"]),
                "values": rec["vector"],
                "metadata": {
                    "source_file":  rec["source_file"],
                    "header_path":  rec["header_path"],
                    "token_count":  rec["token_count"],
                    "oversized":    rec["oversized"],
                    "text":         rec["text"],
                },
            }
            for rec in batch
        ]
        index.upsert(vectors=vectors)
        total_upserted += len(vectors)
        print(f"  Batch {batch_idx + 1}/{len(batches)}: "
              f"upserted {len(vectors)}  (running total: {total_upserted})")

    print(f"\nUpsert complete. Total sent: {total_upserted}")

    # Allow Pinecone a moment to reflect the upsert in stats
    print("Waiting 10s for index stats to settle ...")
    time.sleep(10)

    stats = index.describe_index_stats()
    vector_count = stats.get("total_vector_count", stats.total_vector_count if hasattr(stats, "total_vector_count") else "?")
    print(f"\nIndex stats after upsert:")
    print(f"  total_vector_count: {vector_count}")
    if vector_count == n:
        print(f"  MATCH: {vector_count} == {n} expected -- PASS")
    else:
        print(f"  MISMATCH: got {vector_count}, expected {n} -- investigate")


# ── Query / retrieval sanity check ────────────────────────────────────────────

def run_retrieval_check(
    index: Any,
    oai_client: OpenAI,
    queries: list[str],
    top_k: int = 5,
) -> None:
    """
    Embed each query with text-embedding-3-small (same model as corpus),
    query the index, and print top_k results for eyeball relevance check.
    """
    for q_idx, query in enumerate(queries, 1):
        print(f"\n{'='*70}")
        print(f"Query {q_idx}: {query}")
        print(f"{'='*70}")

        # Embed the query (symmetric: no input_type needed)
        resp = oai_client.embeddings.create(input=[query], model=EMBED_MODEL)
        q_vec = resp.data[0].embedding

        results = index.query(
            vector=q_vec,
            top_k=top_k,
            include_metadata=True,
        )

        for rank, match in enumerate(results.matches, 1):
            meta   = match.metadata
            score  = match.score
            src    = meta.get("source_file", "?")
            hdr    = meta.get("header_path", "?")
            text   = meta.get("text", "")
            snippet = text[:100].replace("\n", " ") + ("..." if len(text) > 100 else "")
            print(f"\n  Rank {rank}  score={score:.4f}")
            print(f"  source:  {src}")
            print(f"  header:  {hdr}")
            print(f"  text:    {snippet}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

    load_dotenv()

    pinecone_key = os.getenv("PINECONE_API_KEY")
    openai_key   = os.getenv("OPENAI_API_KEY")

    if not pinecone_key:
        print("PINECONE_API_KEY not found -- check .env"); sys.exit(1)
    if not openai_key:
        print("OPENAI_API_KEY not found -- check .env"); sys.exit(1)

    print(f"PINECONE_API_KEY loaded ({len(pinecone_key)} chars)")
    print(f"OPENAI_API_KEY   loaded ({len(openai_key)} chars)\n")

    pc         = Pinecone(api_key=pinecone_key)
    oai_client = OpenAI(api_key=openai_key)

    stage = sys.argv[1] if len(sys.argv) > 1 else "1"

    # ── Stage 1: create index ─────────────────────────────────────────────────
    if stage == "1":
        print("=" * 60)
        print("STAGE 1 -- create Pinecone index")
        print("=" * 60 + "\n")

        get_or_create_index(pc)

        print("\nSTAGE 1 COMPLETE -- confirm description above, then run '2' to upsert")

    # ── Stage 2: upsert ───────────────────────────────────────────────────────
    elif stage == "2":
        print("=" * 60)
        print("STAGE 2 -- upsert 584 vectors")
        print("=" * 60 + "\n")

        print(f"Loading embeddings from {EMBEDDINGS_FILE} ...")
        records = [
            json.loads(line)
            for line in EMBEDDINGS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(f"Records loaded: {len(records)}\n")

        index = pc.Index(INDEX_NAME)
        upsert_vectors(index, records)

        print("\nSTAGE 2 COMPLETE -- confirm vector count, then run '3' for retrieval check")

    # ── Stage 3: retrieval sanity check ───────────────────────────────────────
    elif stage == "3":
        print("=" * 60)
        print("STAGE 3 -- retrieval sanity check (3 queries, top-5 each)")
        print("=" * 60)

        index = pc.Index(INDEX_NAME)

        queries = [
            "How do I define a path parameter in FastAPI?",
            "How do I handle errors and return a custom HTTP status code?",
            "How do I use Pydantic models for request bodies?",
        ]

        run_retrieval_check(index, oai_client, queries, top_k=5)

        print(f"\n{'='*70}")
        print("STAGE 3 COMPLETE -- eyeball relevance above")

    else:
        print(f"Unknown stage '{stage}'. Use '1', '2', or '3'.")
        sys.exit(1)
