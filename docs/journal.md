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
- The corpus passed file-count and total-size checks while containing wrong-language content (german); only a content-level scan caught it. Count and size are integrity checks, not correctness checks.

**Next:**
- Phase 2: chunking strategy (src/production_rag_forensics/retrieval/chunker.py), starting with one strategy over the 151-file corpus, then embedding and Pinecone indexing.

## 2026-05-21 — Resolve doc include-directives into faithful prose+code corpus

**Worked on:** Extended corpus ingestion to resolve FastAPI's {* ... *} include-directives, inlining referenced example code so the corpus matches what the rendered docs show.

**Decisions:**
- Resolved all include-directives at ingestion time rather than leaving pointers — a docs corpus that omits the code it teaches with is not faithful to the documentation a reader experiences. Resolution is part of canonical ingestion, not a separate step.
- hl[] directives inline the whole referenced file; ln[] directives inline only the selected line range (1-indexed inclusive); bare directives inline the whole file. This matches what the docs site renders.
- Dropped hl[] highlight metadata entirely rather than annotating it inline — highlighting is website presentation with no faithful plain-text equivalent; annotating it would pollute otherwise-clean example code.
- Excluded release-notes.md from the corpus — a 160K-token changelog, not user-facing documentation.
- Resolver fails loud on any unparseable or unresolvable directive rather than emitting a partial corpus.

**Measurements:**
- 433/433 directives resolved; 0 unresolved remaining. Source: ingestion verification grep.
- Code-vs-prose ratio rose from 4.8% to 31.7% of characters inside fenced code blocks — the directives carried the majority of the corpus's code content. Source: post-resolution corpus scan.
- Corpus: 150 markdown files, manifest records directives_resolved=433 and excluded_files. Total bytes fell (1449KB to 1034KB) despite adding code, because excluding the 160K-token release-notes.md removed more than the inlined code added.

**What surprised me:**
- Initial inspection assumed inlining whole files for all directives; the corpus actually contained ln[] content-selector directives where whole-file inlining would show code the docs deliberately hid. Inspecting the real directive variants before building the resolver caught this.

**Next:**
- Chunking strategy decision (src/production_rag_forensics/retrieval/chunker.py), made against the now-final corpus shape (31.7% code), with code-block integrity as a primary concern.
