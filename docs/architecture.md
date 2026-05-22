# Architecture

## Component stack

| Layer | Choice | Rationale |
|---|---|---|
| Orchestration | LangGraph | Stateful agent workflows, rising standard. Forensic question: where does the abstraction help vs. get in the way? |
| Vector DB | Pinecone (managed) | Most-hired-for managed vector DB. Real production cost surface. |
| Observability | Langfuse (self-hosted via Docker) | Open-source, transferable signal. Production-grade tracing without locking to a framework vendor. |
| Inference (primary) | Claude Sonnet 4.6 | With prompt caching enabled. Best-value flagship. |
| Inference (comparison) | GPT-5.4, Gemini 3.1 Pro | Cross-provider measurement is part of the analytical core. |
| Reranking tier | None (dense-only, locked 2026-05-22) | Both Haiku LLM and cross-encoder rerankers measured neutral-to-negative on cross_reference. Dense-only wins. Reranker code retained. |
| Service layer | FastAPI | Production interface, also dogfooding the corpus. |
| Agent interface | Custom MCP server (Streamable HTTP) | Exposes journal/failure-mode/cost as agent-queryable tools. |
| Corpus | FastAPI documentation (English, release 0.136.1, 150 files, include-directives resolved to inline example code) | Heterogeneous structure (prose, code, API refs, tutorials). Verifiable without domain ramp-up. |

## Data flow

### Ingestion path

```
FastAPI docs (cloned repo, pinned commit)
  → chunker (section-based, ## / ###, merge floor ~200 tok, target ~512 tok, B1 fence protection)
    → OpenAI text-embedding-3-small (1536-dim, symmetric, own-embedder call)
      → Pinecone serverless index: fastapi-docs-v1 (1536-dim, cosine, AWS us-east-1, 584 vectors)
```

### Query path

```
User query
  → FastAPI service layer
    → LangGraph agent loop
      → Pinecone hybrid retrieval (sparse + dense)
          → Claude Sonnet 4.6 generation (+ GPT-5.4, Gemini 3.1 Pro for comparison)
            → Response
                │
                └── Langfuse tracing across all stages
```

## Rejected alternatives

| Alternative | Reason for rejection |
|---|---|
| Self-hosted inference (vLLM, Ollama) | Operational complexity without analytical payoff |
| Parallel raw-SDK implementation | Busywork — no additional signal over framework-based approach |
| Multiple vector DB ablation (Pinecone + Weaviate + pgvector) | Pick one, document the choice; ablation is not the research question |
| Fine-tuning | Out of scope for a retrieval study |
| Multimodal (vision, audio) | Corpus is text + code |
| Kubernetes | Docker Compose is sufficient; K8s would signal over-engineering |
| UI polish | Minimal demo is fine; polish is not the deliverable |

## Locked design decisions

**Chunking (locked 2026-05-21):** Section-based on markdown `##` / `###` headers. Merge floor ~200 tokens (adjacent sections within a file combined until floor met). Target ~512 tokens. Never split a fenced code block — oversized chunks allowed and flagged (B1). Zero overlap. Token counting via tiktoken cl100k_base. Produces 584 chunks from 150 files (mean 427 tok, median 412 tok). Reasoning in journal 2026-05-21.

**Embedding (locked 2026-05-21):** OpenAI text-embedding-3-small, 1536 dims, symmetric (same call for corpus and queries; no input_type). Implemented as an explicit own-embedder call — not Pinecone integrated embedding — to preserve per-stage cost measurement. General model chosen over code-specialized alternatives; the corpus is majority prose and code-specialist embedding risks a prose penalty. Code-specialized embedding deferred as a measured upgrade if the syntactic eval category underperforms. Reasoning in journal 2026-05-21.

## Open architectural questions

- **Code-specialized embedding:** text-embedding-3-small is the locked choice; a code-specialist model (e.g. voyage-code-3) is a held upgrade if syntactic eval category underperforms in Phase 2 baseline.
- **Reranking threshold:** At what reranker score should retrieved chunks be filtered out? Requires baseline eval data to set empirically.
- **Hybrid search weighting:** Sparse/dense balance for Reciprocal Rank Fusion (RRF). The right alpha depends on query category distribution and needs per-category measurement to tune.
- **Reranker model choice.** RESOLVED (2026-05-22): Moot. Both Haiku LLM reranker and cross-encoder (ms-marco-MiniLM-L-6-v2) are neutral-to-negative on cross_reference (mean faithfulness 3.87 and 3.70 respectively vs. dense-only 3.90). Neither earns its place. Pipeline default is dense-only retrieval. Reranker code retained for future experiments.
- **Does reranking earn its place at all?** RESOLVED (2026-05-22): No. Dense-only retrieval wins on cross_reference (mean faithfulness 3.90 vs. 3.87 Haiku, 3.70 cross-encoder). The binding constraint is corpus structure (knowledge fragmentation), not retrieval signal quality. Reranking cannot surface integration content that does not exist as a retrievable unit.
