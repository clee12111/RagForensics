"""
LangGraph orchestration for the FastAPI RAG pipeline.

Graph: START → embed_query → retrieve → generate → END

State schema (TypedDict):
    query:             str           — the user's question
    query_embedding:   list[float]   — 1536-dim vector from text-embedding-3-small
    retrieved_chunks:  list[dict]    — top-k chunks: {text, source_file, header_path, score}
    answer:            str           — Claude Sonnet 4.6 grounded response

Public API:
    run_query(query: str) -> dict
        Returns {"query": str, "answer": str, "chunks": list[dict]}

Tracing:
    Each node wraps its work in a Langfuse observation span.
    If Langfuse is unavailable (Docker not running / keys missing), tracing
    is silently skipped and the graph runs normally.
"""

from __future__ import annotations

import os
from typing import Optional

from anthropic import Anthropic
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pinecone import Pinecone
from typing_extensions import TypedDict

from production_rag_forensics.observability.client import get_client, langfuse_span

load_dotenv()

# ── Client singletons (module-level; one init per process) ────────────────────

_oai      = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
_pc       = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
_index    = _pc.Index("fastapi-docs-v1")
_anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

EMBED_MODEL   = "text-embedding-3-small"
CLAUDE_MODEL  = "claude-sonnet-4-6"
TOP_K         = 5

# System prompt as a structured block with cache_control.
# The system prompt is the stable, per-deployment surface — same text on every
# query — so it is the right thing to cache.  Retrieved chunks rotate per query
# and are NOT cached: caching rotating context would thrash the cache (a new
# cache entry per unique chunk set) and waste money with no hit benefit.
SYSTEM_PROMPT_BLOCK = [
    {
        "type": "text",
        "text": (
            "You are a precise technical assistant answering questions about the FastAPI framework. "
            "Answer ONLY using the provided context. "
            "If the answer is not in the context, say so explicitly."
        ),
        "cache_control": {"type": "ephemeral"},
    }
]

# ── State ─────────────────────────────────────────────────────────────────────

class RAGState(TypedDict):
    query:             str
    query_embedding:   Optional[list[float]]
    retrieved_chunks:  Optional[list[dict]]
    answer:            Optional[str]
    usage:             Optional[dict]   # {input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens}


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
    """Dense retrieval: query Pinecone top_k=5, return chunks with metadata."""
    lf = get_client()
    vec = state["query_embedding"]

    with langfuse_span(lf, name="retrieve", obs_type="retriever",
                       input={"top_k": TOP_K, "index": "fastapi-docs-v1"}):
        result = _index.query(vector=vec, top_k=TOP_K, include_metadata=True)

        chunks = [
            {
                "text":        match.metadata.get("text", ""),
                "source_file": match.metadata.get("source_file", ""),
                "header_path": match.metadata.get("header_path", ""),
                "score":       match.score,
            }
            for match in result.matches
        ]

        if lf:
            lf.update_current_span(
                output={
                    "chunks_returned": len(chunks),
                    "scores": [round(c["score"], 4) for c in chunks],
                    "sources": [c["source_file"] for c in chunks],
                }
            )

    return {**state, "retrieved_chunks": chunks}


def generate(state: RAGState) -> RAGState:
    """Generate a grounded answer from retrieved chunks via Claude Sonnet 4.6."""
    lf = get_client()
    query  = state["query"]
    chunks = state["retrieved_chunks"] or []

    formatted = "\n\n".join(
        f"[Chunk {i + 1} — {c['source_file']}]\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    user_message = f"Question: {query}\n\nContext:\n{formatted}\n\nAnswer:"

    with langfuse_span(lf, name="generate", obs_type="generation",
                       input={"chunk_count": len(chunks), "model": CLAUDE_MODEL}):
        response = _anthropic.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            temperature=0,
            system=SYSTEM_PROMPT_BLOCK,
            messages=[{"role": "user", "content": user_message}],
        )
        answer = response.content[0].text

        usage = response.usage
        cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read    = getattr(usage, "cache_read_input_tokens", 0) or 0

        if lf:
            lf.update_current_generation(
                output={"answer": answer},
                model=CLAUDE_MODEL,
                usage_details={
                    "input":          usage.input_tokens,
                    "output":         usage.output_tokens,
                    "cache_creation": cache_created,
                    "cache_read":     cache_read,
                },
            )

    return {
        **state,
        "answer": answer,
        "usage": {
            "input_tokens":           usage.input_tokens,
            "output_tokens":          usage.output_tokens,
            "cache_creation_tokens":  cache_created,
            "cache_read_tokens":      cache_read,
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
            "chunks":                 list[dict],  # {text, source_file, header_path, score}
            "input_tokens":           int,
            "output_tokens":          int,
            "cache_creation_tokens":  int,
            "cache_read_tokens":      int,
        }

    If Langfuse is configured, wraps the entire invocation in a parent trace
    so all three node spans appear as children in the UI.
    """
    lf = get_client()

    initial_state: RAGState = {
        "query":            query,
        "query_embedding":  None,
        "retrieved_chunks": None,
        "answer":           None,
        "usage":            None,
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
    }
