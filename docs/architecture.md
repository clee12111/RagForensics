# Architecture

## Component stack

| Layer | Choice | Rationale |
|---|---|---|
| Orchestration | LangGraph | Stateful agent workflows, rising standard. Forensic question: where does the abstraction help vs. get in the way? |
| Vector DB | Pinecone (managed) | Most-hired-for managed vector DB. Real production cost surface. |
| Observability | Langfuse (self-hosted via Docker) | Open-source, transferable signal. Production-grade tracing without locking to a framework vendor. |
| Inference (primary) | Claude Sonnet 4.6 | With prompt caching enabled. Best-value flagship. |
| Inference (comparison) | GPT-5.5 (flagship), Gemini-3.1-flash-lite (small, thinking_budget=0) | Cross-provider generation study. Tier-confounded by design — production-realistic per-provider choices, not tier-matched. |
| Retrieval | Hybrid: BM25 sparse + Pinecone dense, RRF-merged (k=60) (locked 2026-05-22) | Dense-only misses keyword-exact terms outside cosine neighborhood. BM25 recovers FM-1 retrieval misses. 5/5 FM-1 records improved. Reranking removed (neutral-to-negative on cross_reference). |
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
      → embed_query: OpenAI text-embedding-3-small → 1536-dim vector
        → retrieve: BM25 (rank_bm25, 584-chunk index) + Pinecone dense (top-20 each)
                    → RRF fusion (k=60) → top-5 chunks
          → generate: Claude Sonnet 4.6 + few-shot grounding prompt (cached, ~1,408 tok)
            → Response
                │
                └── Langfuse tracing across all stages (cloud, US region)
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

**Eval-harness chunk text (locked 2026-05-22):** `harness.py` serializes the `text` field of each retrieved chunk into the JSONL record (`chunks_out`). Required for judge faithfulness evaluation — without it, the judge scores against filenames only (citation-style proxy). Bug was invisible during Anthropic-only eval; exposed by cross-provider study when Gemini's answer style diverged from the citation pattern the judge was implicitly rewarding. Fix: `"text": c.get("text", "")` in `chunks_out`. All future eval runs preserve chunk text.

**Chunking (locked 2026-05-21):** Section-based on markdown `##` / `###` headers. Merge floor ~200 tokens (adjacent sections within a file combined until floor met). Target ~512 tokens. Never split a fenced code block — oversized chunks allowed and flagged (B1). Zero overlap. Token counting via tiktoken cl100k_base. Produces 584 chunks from 150 files (mean 427 tok, median 412 tok). Reasoning in journal 2026-05-21.

**Embedding (locked 2026-05-21):** OpenAI text-embedding-3-small, 1536 dims, symmetric (same call for corpus and queries; no input_type). Implemented as an explicit own-embedder call — not Pinecone integrated embedding — to preserve per-stage cost measurement. General model chosen over code-specialized alternatives; the corpus is majority prose and code-specialist embedding risks a prose penalty. Code-specialized embedding deferred as a measured upgrade if the syntactic eval category underperforms. Reasoning in journal 2026-05-21.

## Open architectural questions

- **Code-specialized embedding:** text-embedding-3-small is the locked choice; a code-specialist model (e.g. voyage-code-3) is a held upgrade if syntactic eval category underperforms in Phase 2 baseline.
- **Reranking threshold:** At what reranker score should retrieved chunks be filtered out? Requires baseline eval data to set empirically.
- **Hybrid search weighting:** RESOLVED (2026-05-22): Hybrid (BM25 + dense, RRF k=60) adopted. Recovers FM-1 retrieval misses that dense-only cannot (keyword-exact terms outside dense top-N). Standard RRF, no learned weighting. Measured 5/5 FM-1 records improved. Reveals (does not create) underlying FM-4 on questions with stacked failures.
- **Reranker model choice.** RESOLVED (2026-05-22): Moot. Both Haiku LLM reranker and cross-encoder (ms-marco-MiniLM-L-6-v2) are neutral-to-negative on cross_reference (mean faithfulness 3.87 and 3.70 respectively vs. dense-only 3.90). Neither earns its place. Pipeline default is dense-only retrieval. Reranker code retained for future experiments.
- **Does reranking earn its place at all?** RESOLVED (2026-05-22): No. Dense-only retrieval wins on cross_reference (mean faithfulness 3.90 vs. 3.87 Haiku, 3.70 cross-encoder). The binding constraint is corpus structure (knowledge fragmentation), not retrieval signal quality. Reranking cannot surface integration content that does not exist as a retrievable unit.
- **Cross-provider generation.** RESOLVED (2026-05-22): Anthropic 4.45 / OpenAI 4.39 / Google 4.62 faithfulness on 150q (same retrieval, same prompt). 0.23-point spread across 28x cost ($0.18–$5.06). Generation capability is not the bottleneck on this corpus with strong retrieval+grounding. Gemini-3.1-flash-lite selected over 3.1-pro-preview to eliminate thinking-token budget interference and maintain judge independence (cross-generation separation from gemini-2.5-flash judge).
