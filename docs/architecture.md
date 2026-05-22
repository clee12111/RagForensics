# Architecture

## Component stack

| Layer | Choice | Rationale |
|---|---|---|
| Orchestration | LangGraph | Stateful agent workflows, rising standard. Forensic question: where does the abstraction help vs. get in the way? |
| Vector DB | Pinecone (managed) | Most-hired-for managed vector DB. Real production cost surface. |
| Observability | Langfuse (self-hosted via Docker) | Open-source, transferable signal. Production-grade tracing without locking to a framework vendor. |
| Inference (primary) | Claude Sonnet 4.6 | With prompt caching enabled. Best-value flagship. |
| Inference (comparison) | GPT-5.4, Gemini 3.1 Pro | Cross-provider measurement is part of the analytical core. |
| Reranking tier | Claude Haiku 4.5 (Phase 1 default — pending measurement, see Open questions) | Cheap-tier retrieval signal. |
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
        → Claude Haiku 4.5 reranker
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
- **Reranker model choice.** Haiku 4.5 is the Phase 1 default because the project already has Anthropic access and using a generalist LLM as a reranker is itself an interesting forensic question. Alternatives (Cohere Rerank 3, Voyage Rerank-2) will be considered if Haiku's measurements show it doesn't earn its place. Decision deferred to Phase 2 baseline eval.
- **Does reranking earn its place at all?** Whether the reranking stage materially improves precision@5 over retrieval-only, per question category. To be answered with measurements in Phase 2.
