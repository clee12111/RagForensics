"""
LangGraph orchestration for the FastAPI RAG pipeline.

Graph: START → embed_query → retrieve → generate → END

State schema (TypedDict):
    query:             str           — the user's question
    query_embedding:   list[float]   — 1536-dim vector from text-embedding-3-small
    retrieved_chunks:  list[dict]    — top-k chunks: {text, source_file, header_path, score}
    answer:            str           — grounded response from the configured provider

Public API:
    run_query(query: str) -> dict
        Returns {"query": str, "answer": str, "chunks": list[dict], ...}

Configuration:
    GENERATION_PROVIDER — set to "anthropic" (default), "openai", or "google"
    to swap the generation backend while holding retrieval constant.

Tracing:
    Each node wraps its work in a Langfuse observation span.
    If Langfuse is unavailable (Docker not running / keys missing), tracing
    is silently skipped and the graph runs normally.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pinecone import Pinecone
from typing_extensions import TypedDict

from production_rag_forensics.observability.client import get_client, langfuse_span
from production_rag_forensics.orchestration.generation import generate_answer

load_dotenv()

# ── Provider flag ─────────────────────────────────────────────────────────────
# Change this to swap the generation backend. Retrieval is held constant.
# "anthropic" — claude-sonnet-4-6 with prompt caching (default)
# "openai"    — gpt-5.5
# "google"    — gemini-3.1-pro-preview

GENERATION_PROVIDER = os.environ.get("GENERATION_PROVIDER", "anthropic")

# ── Client singletons (module-level; one init per process) ────────────────────

_oai   = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
_pc    = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
_index = _pc.Index("fastapi-docs-v1")

EMBED_MODEL        = "text-embedding-3-small"
TOP_K              = 5
RERANKER_BACKEND   = "hybrid"          # "hybrid" | "none" | "cross_encoder" | "haiku"
_FETCH_K: dict[str, int] = {
    "cross_encoder": 50,   # batched pass is cheap — wider candidate pool
    "haiku":         20,   # 20 sequential API calls per query
    "none":           5,   # dense top-5 directly
    "hybrid":         20,  # dense_n for Pinecone; BM25 runs over all 584 locally
}

# System prompt text — passed as plain text to generate_answer().
# Each provider implementation handles its own caching wrapper:
#   Anthropic: wraps in cache_control block (ephemeral); activates at >1024 tokens.
#   OpenAI:    uses automatic prompt caching — no wrapper needed.
#   Google:    no caching at RAG prompt size; passed as system_instruction.
#
# The four worked examples push the prompt well above the 1024-token Anthropic floor.
_SYSTEM_PROMPT_TEXT = """\
You are a precise technical assistant answering questions about the FastAPI framework.

RULES — apply to every response:
1. Answer ONLY using information present in the provided context chunks.
2. Do not introduce concepts, types, methods, or behavioral claims that are absent from any chunk, even if you know them from training.
3. If the chunks explicitly state a fact, trust the chunk and reflect it accurately — do not override chunk content with your prior knowledge.
4. When you make a claim, it must be directly traceable to content in a specific chunk.
5. If the context only partially covers the question, explain what the chunks do cover and explicitly state what they do not cover.
6. If no chunk addresses the question at all, say: "The provided context does not contain information to answer this question."

---

WORKED EXAMPLES — these show the required response pattern for four distinct situations:

### Example 1: Full grounded answer

Question: How do I declare a path parameter in FastAPI?

Context:
[Chunk 1 — path-params.md]
You can declare path "parameters" or "variables" with the same syntax used by Python format strings:

    @app.get("/items/{item_id}")
    async def read_item(item_id: int):
        return {"item_id": item_id}

FastAPI will parse and validate the value. If item_id cannot be coerced to int, it returns a JSON error automatically.

Correct response:
Declare a path parameter by including it in the URL template with curly braces — `"/items/{item_id}"` — and adding the same name as a typed function argument. FastAPI reads the type annotation and validates the request automatically. If the value cannot be coerced to `int`, FastAPI returns a JSON validation error. No additional validation code is required (chunk 1, path-params.md).

---

### Example 2: Honest partial — context covers the diagnosis but not the fix

Question: I'm combining UploadFile with a background task to process the file, but by the time the background task runs, the temporary file descriptor is already closed. How do I manage the lifecycles?

Context:
[Chunk 1 — request-files.md]
UploadFile uses Python's SpooledTemporaryFile internally. The file object is open and readable during request processing.

[Chunk 2 — background-tasks.md]
Background tasks run after the response has been sent to the client. They execute outside the request/response lifecycle.

Correct response:
The chunks explain the cause: `UploadFile` wraps a `SpooledTemporaryFile` that is open during request handling (chunk 1, request-files.md), but background tasks run after the response is returned, outside the request lifecycle (chunk 2, background-tasks.md). The file descriptor is closed before the task executes.

The provided context does not describe a solution for this lifecycle mismatch. It contains no instructions for reading file contents before dispatch, buffering the data, or passing a copy to the background task. I cannot recommend a specific implementation pattern from these chunks.

---

### Example 3: Anti-injection — context is partial; do not supplement with training knowledge

Question: I have a field typed as Union[int, str]. When a user passes a string that looks like a number, Pydantic is forcing it into an integer. How do I enforce strict type matching?

Context:
[Chunk 1 — body.md]
For Union types, Pydantic tries each type in the order they are listed. If the value can be coerced to the first type, it uses that. A string "42" will be coerced to int 42 for Union[int, str].

[Chunk 2 — field-types.md]
Use Field() to add validation constraints such as minimum and maximum numeric values.

Correct response:
The chunks explain the behavior: for `Union[int, str]`, Pydantic tries each type in declaration order and uses the first successful coercion. A string like `"42"` coerces to `int`, so the `str` branch is never reached (chunk 1, body.md).

The provided context does not describe a mechanism to disable coercion or enforce strict per-branch type matching in a Union field. `Field()` is mentioned for numeric constraints (chunk 2, field-types.md), not for strict union resolution. The context does not cover this case further.

---

### Example 4: Contradiction guard — the chunk states X; reproduce X, do not override it

Question: Is there a difference in execution order between a dependency declared at the application level versus one in a router's dependencies list?

Context:
[Chunk 1 — bigger-applications.md]
Dependencies declared in APIRouter(dependencies=[...]) run first, for all routes within that router. Dependencies declared in FastAPI(dependencies=[...]) apply to the entire application and run after the router-level dependencies have already executed.

Correct response:
Yes. Router-level dependencies — declared in `APIRouter(dependencies=[...])` — execute first, scoped to routes within that router. Application-level dependencies — declared in `FastAPI(dependencies=[...])` — execute after router-level dependencies and apply to every route in the application (chunk 1, bigger-applications.md).

---

END OF EXAMPLES

Answer the question using only the provided context chunks. Apply the pattern above: trace every claim to a chunk, be explicit when the context does not fully cover the question, and do not introduce any type, method, or behavioral detail that is absent from the chunks.\
"""

# ── State ─────────────────────────────────────────────────────────────────────

class RAGState(TypedDict):
    query:             str
    query_embedding:   Optional[list[float]]
    retrieved_chunks:  Optional[list[dict]]
    answer:            Optional[str]
    usage:             Optional[dict]   # {input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens}
    reranker_cost_usd: Optional[float]
    reranked:          Optional[bool]


# ── Graph nodes ───────────────────────────────────────────────────────────────

def embed_query(state: RAGState) -> RAGState:
    """Embed the user query with text-embedding-3-small."""
    lf = get_client()
    query = state["query"]

    with langfuse_span(lf, name="embed_query", obs_type="embedding",
                       input={"query": query}):
        response = _oai.embeddings.create(input=[query], model=EMBED_MODEL)
        vec = response.data[0].embedding

        if lf:
            lf.update_current_span(
                output={"dimension": len(vec), "model": EMBED_MODEL}
            )

    return {**state, "query_embedding": vec}


def retrieve(state: RAGState) -> RAGState:
    """
    Dense retrieval from Pinecone, optionally followed by reranking or hybrid fusion.

    RERANKER_BACKEND="none":          fetch top_k=5, return directly.
    RERANKER_BACKEND="hybrid":        BM25 + dense, RRF-merged top_k=5 (no API cost).
    RERANKER_BACKEND="haiku":         fetch top_k=20, rerank via Haiku 4.5 (20 API calls).
    RERANKER_BACKEND="cross_encoder": fetch top_k=50, rerank via cross-encoder (1 batched pass).
    """
    lf = get_client()
    vec  = state["query_embedding"]
    query_text = state["query"]
    fetch_k = _FETCH_K[RERANKER_BACKEND]

    with langfuse_span(lf, name="retrieve", obs_type="retriever",
                       input={"fetch_k": fetch_k, "backend": RERANKER_BACKEND,
                              "index": "fastapi-docs-v1"}):

        reranker_cost = 0.0

        if RERANKER_BACKEND == "hybrid":
            from production_rag_forensics.retrieval.hybrid_search import get_hybrid_search
            chunks = get_hybrid_search(_index).search(
                query=query_text,
                query_embedding=vec,
                top_k=TOP_K,
                dense_n=20,
                sparse_n=20,
            )

        else:
            result = _index.query(vector=vec, top_k=fetch_k, include_metadata=True)

            chunks = [
                {
                    "text":        match.metadata.get("text", ""),
                    "source_file": match.metadata.get("source_file", ""),
                    "header_path": match.metadata.get("header_path", ""),
                    "score":       match.score,
                }
                for match in result.matches
            ]

            if RERANKER_BACKEND == "haiku":
                from production_rag_forensics.retrieval.reranker import Reranker
                reranker = Reranker()
                chunks = reranker.rerank(query_text, chunks, top_k=TOP_K)
                reranker_cost = chunks[0].pop("rerank_cost_usd", 0.0) if chunks else 0.0

            elif RERANKER_BACKEND == "cross_encoder":
                from production_rag_forensics.retrieval.cross_encoder_reranker import get_reranker
                chunks = get_reranker().rerank(query_text, chunks, top_k=TOP_K)
                # cross-encoder has no API cost

        if lf:
            def _top_score(c: dict) -> float:
                for key in ("cross_encoder_score", "reranker_score"):
                    if key in c:
                        return round(c[key], 4)
                return round(c["score"], 4)
            lf.update_current_span(
                output={
                    "chunks_returned": len(chunks),
                    "backend": RERANKER_BACKEND,
                    "reranker_cost_usd": reranker_cost,
                    "scores": [_top_score(c) for c in chunks],
                    "sources": [c["source_file"] for c in chunks],
                }
            )

    return {
        **state,
        "retrieved_chunks":  chunks,
        "reranker_cost_usd": reranker_cost,
        "reranked":          RERANKER_BACKEND != "none",
    }


def generate(state: RAGState) -> RAGState:
    """Generate a grounded answer from retrieved chunks via the configured provider."""
    lf = get_client()
    query  = state["query"]
    chunks = state["retrieved_chunks"] or []

    formatted = "\n\n".join(
        f"[Chunk {i + 1} — {c['source_file']}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    user_message = f"Question: {query}\n\nContext:\n{formatted}\n\nAnswer:"

    with langfuse_span(lf, name="generate", obs_type="generation",
                       input={"chunk_count": len(chunks), "provider": GENERATION_PROVIDER}):
        result = generate_answer(GENERATION_PROVIDER, _SYSTEM_PROMPT_TEXT, user_message)

        if lf:
            lf.update_current_generation(
                output={"answer": result.answer},
                model=result.model,
                usage_details={
                    "input":          result.input_tokens,
                    "output":         result.output_tokens,
                    "cache_creation": result.cache_creation_tokens,
                    "cache_read":     result.cache_read_tokens,
                },
            )

    return {
        **state,
        "answer": result.answer,
        "usage": {
            "input_tokens":           result.input_tokens,
            "output_tokens":          result.output_tokens,
            "cache_creation_tokens":  result.cache_creation_tokens,
            "cache_read_tokens":      result.cache_read_tokens,
            "cost_usd":               result.cost_usd,
            "generation_provider":    result.provider,
            "generation_model":       result.model,
        },
    }


# ── Graph assembly ────────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    g = StateGraph(RAGState)
    g.add_node("embed_query", embed_query)
    g.add_node("retrieve",    retrieve)
    g.add_node("generate",    generate)
    g.add_edge(START,         "embed_query")
    g.add_edge("embed_query", "retrieve")
    g.add_edge("retrieve",    "generate")
    g.add_edge("generate",    END)
    return g.compile()


_graph = _build_graph()


# ── Public API ────────────────────────────────────────────────────────────────

def run_query(query: str) -> dict:
    """
    Run the full RAG pipeline for a single query.

    Returns:
        {
            "query":                  str,
            "answer":                 str,
            "chunks":                 list[dict],  # {text, source_file, header_path, score, ...}
            "input_tokens":           int,
            "output_tokens":          int,
            "cache_creation_tokens":  int,
            "cache_read_tokens":      int,
            "cost_usd":               float,       # generation cost only
            "generation_provider":    str,         # "anthropic" | "openai" | "google"
            "generation_model":       str,         # exact model string
            "reranker_cost_usd":      float,       # 0.0 if RERANKER_BACKEND="none" or "cross_encoder"
            "reranked":               bool,        # False if RERANKER_BACKEND="none"
        }

    If Langfuse is configured, wraps the entire invocation in a parent trace
    so all three node spans appear as children in the UI.
    """
    lf = get_client()

    initial_state: RAGState = {
        "query":             query,
        "query_embedding":   None,
        "retrieved_chunks":  None,
        "answer":            None,
        "usage":             None,
        "reranker_cost_usd": None,
        "reranked":          None,
    }

    if lf:
        with lf.start_as_current_observation(
            name="rag-query",
            as_type="span",
            input={"query": query},
        ):
            final = _graph.invoke(initial_state)
            lf.update_current_span(output={"answer": final["answer"]})
            lf.flush()
    else:
        final = _graph.invoke(initial_state)

    usage = final.get("usage") or {}
    return {
        "query":                  final["query"],
        "answer":                 final["answer"],
        "chunks":                 final["retrieved_chunks"] or [],
        "input_tokens":           usage.get("input_tokens", 0),
        "output_tokens":          usage.get("output_tokens", 0),
        "cache_creation_tokens":  usage.get("cache_creation_tokens", 0),
        "cache_read_tokens":      usage.get("cache_read_tokens", 0),
        "cost_usd":               usage.get("cost_usd", 0.0),
        "generation_provider":    usage.get("generation_provider", GENERATION_PROVIDER),
        "generation_model":       usage.get("generation_model", ""),
        "reranker_cost_usd":      final.get("reranker_cost_usd") or 0.0,
        "reranked":               final.get("reranked") or False,
    }
