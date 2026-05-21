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

## 2026-05-20 — Environment, packaging, and corpus ingestion

**Worked on:** Python environment setup, package layout, and FastAPI docs corpus ingestion.

**Decisions:**
- Adopted src-layout package (src/production_rag_forensics/) over the flat functional layout — required for hatchling editable install; preserves functional submodules one level deeper.
- Pinned corpus to FastAPI release tag 0.136.1 (commit e54e5a89) rather than main HEAD — a tagged release corresponds to a shipped version users actually run, avoiding contamination from unreleased/incomplete docs.
- Restricted corpus to the English docs tree (docs/en/docs) by structural guarantee, not language detection — a runtime guard rejects any collected path containing a non-English language segment, making contamination fail loud rather than silent.
- Excluded repo scaffolding from the corpus (underscore-prefixed files and translation-banner.md) — these are tooling/UI artifacts, not user-facing documentation.

**Measurements:**
- Corpus: 151 English markdown files, 1449.4 KB, FastAPI 0.136.1 (commit e54e5a8980ffa6d7ff68ee7b25a1c46036375521). Source: data/corpus/manifest.json.
- 2 files excluded (_llm-test.md, translation-banner.md), recorded in manifest excluded_files.
- Dependency install: clean on Python 3.13.3, all wheels prebuilt, no source compilation.

**What surprised me:**
- The corpus passed file-count and total-size checks while containing wrong-language content; only a content-level scan caught it. Count and size are integrity checks, not correctness checks.

**Next:**
- Phase 2: chunking strategy (src/production_rag_forensics/retrieval/chunker.py), starting with one strategy over the 151-file corpus, then embedding and Pinecone indexing.
