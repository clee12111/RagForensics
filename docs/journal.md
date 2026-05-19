# Engineering Journal

## 2026-05-19 — Repo scaffolding and initial documentation

**Worked on:** repo scaffolding and initial documentation

**Decisions:**
- LangGraph for orchestration — stateful agent workflows, rising standard; forensic question is where the abstraction helps vs. gets in the way
- Pinecone (managed) for vector DB — most-hired-for managed vector DB, real production cost surface
- Langfuse (self-hosted via Docker) for observability — open-source, transferable signal, production-grade tracing without vendor lock-in
- Claude Sonnet 4.6 as primary inference with prompt caching — best-value flagship
- GPT-5.4 and Gemini 3.1 Pro for cross-provider comparison — cross-provider measurement is part of the analytical core
- Claude Haiku 4.5 for reranking — cheap-tier retrieval signal
- FastAPI for service layer — production interface, also dogfooding the corpus
- Custom MCP server (Streamable HTTP transport) — exposes journal/failure-mode/cost as agent-queryable tools
- FastAPI documentation as corpus — heterogeneous structure, verifiable without domain ramp-up
- Custom eval harness (no RAGAS) — full control over scoring methodology and reporting

**Measurements:**
- None — scaffolding only

**What surprised me:**
- Nothing this session

**Next:**
- Corpus ingestion script (scripts/ingest_corpus.py), targeting cloned fastapi/fastapi docs/ directory pinned to a specific commit hash for reproducibility
