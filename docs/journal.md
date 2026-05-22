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

## 2026-05-21 — Chunking strategy: section-based with fence-state-aware splitting

**Worked on:** Designed and verified the chunking strategy against the resolved corpus, building src/production_rag_forensics/retrieval/chunker.py.

**Decisions:**
- Section-based chunking (split on markdown ## and ### headers) — corpus inspection showed sections are pre-sized sanely (nothing structurally over 2000 est. tokens) and code is tightly interleaved with explanatory prose, so author-defined section boundaries are natural, coherent chunk units. Matches the production-standard "let the document author decide cut points."
- Merge floor ~200 tokens — 61% of sections are under 200 tokens; without merging, the corpus would produce context-starved micro-chunks (benchmarks show sub-100-token fragments score far worse). Small adjacent sections merge within a file to meet the floor.
- Target ~512 tokens — the validated production default for chunk size.
- Never split a fenced code block (B1) — preserving code integrity is the point of having resolved the includes; a torn code block produces half-function retrieval and hallucinated completions. Oversized chunks are allowed and logged rather than tearing code.
- Zero overlap to start — section boundaries are clean semantic cuts that don't require overlap to heal; overlap is deferred as a measurable experiment.
- Switched token counting from a chars/4 estimate to tiktoken (cl100k_base) — chars/4 undercounts code badly (one code-dense section measured 1461 est. vs 2831 real tokens, 93% off); real token counts are needed because chunk-size decisions and downstream limits are in real tokens. Added tiktoken as a dependency.

**Measurements:**
- 584 chunks from 150 files. Mean 427 tokens, median 412. Distribution: 73% in the 200-512 target band.
- 61 chunks under the 200 floor, all classified as legitimate remnants (25 whole-file too small to merge, 36 end-of-file), zero merging bugs.
- 1 chunk exceeds 2000 tokens (stream-data.md "Simulate a File", 2831 tokens, past the ~2500 "context cliff") — preserved whole per B1, flagged as a Phase 2 retrieval-quality watch item.
- Fence-parity verification: 0 of 584 chunks have an odd code-fence count — torn code proven impossible across the corpus.

**What surprised me:**
- The first chunker used a naive header regex that matched "#" comment lines inside Python/shell code blocks as section headers, silently splitting 8 code blocks across chunk boundaries (26 odd-fence chunks). The aggregate distribution looked healthy; only the exhaustive fence-parity check caught it. Fixed by tracking fence open/close state line-by-line and recognizing headers only at fence-depth zero. Healthy aggregate metrics are not a correctness check — only content-level verification is.

**Next:**
- Embedding model decision and Pinecone indexing (src/production_rag_forensics/retrieval/embedder.py), and a Pinecone connection smoke test. Verify .env has the Pinecone key first.

## 2026-05-21 — Embedding stage: provider selection and corpus indexing prep

**Worked on:** Selected the embedding provider/model and built the embedder (src/production_rag_forensics/retrieval/embedder.py), embedding all 584 chunks to 1536-dim vectors persisted for indexing.

**Decisions:**
- Embedding via an explicit own-embedder call (not Pinecone integrated embedding) — the eval requires cost-per-pipeline-stage, and integrated embedding would fuse embedding cost into Pinecone operations, making the embedding stage unmeasurable as a separate cost. Vector visibility in Pinecone is unaffected either way; the decision is about cost-attribution, not vector accessibility.
- Provider switched Voyage → OpenAI text-embedding-3-small mid-session. Voyage (voyage-3.5, 1024-dim) was selected first for its free tier, but its no-payment free tier is 3 RPM / 10K TPM — a single 128-chunk batch (~61K tokens) is 6x the per-minute token budget, which backoff cannot rescue because the problem is request size, not request frequency. OpenAI was chosen because: the $5 deposit is needed for Phase 5 GPT generation anyway (serves double duty), Tier 1 limits (1M TPM / 3000 RPM) eliminate throttling at corpus scale, cost is negligible (~$0.005 for the full corpus), and a hard $5 monthly cap was set as structural spend protection.
- Model text-embedding-3-small at full 1536 dims, no dimension reduction — dimension is a storage/speed/quality lever that is irrelevant at this corpus size; full quality, no reason to reduce.
- General embedding model, not a code-specialized one, despite the 31.7%-code corpus — chunks are mixed prose+code and the majority is prose; a code specialist risks a prose penalty. Code-specialized embedding is held as a measured upgrade if the syntactic eval category later underperforms.

**Measurements:**
- 584 chunks embedded, all dim exactly 1536. Source: embedder Stage 2 verification.
- 253,134 tokens (tiktoken cl100k_base), implied cost $0.005 at $0.02/1M — the embedding-stage cost number.
- 5 requests, 3.9s wall-clock, zero rate-limit backoff at OpenAI Tier 1.
- Persisted 584 complete records (vector + chunk text + metadata) to data/embeddings/chunks_embedded.jsonl, 18.4 MB.

**What surprised me:**
- The real Voyage free-tier limit (3 RPM / 10K TPM) only became unambiguous when the actual corpus run returned the explicit error string — the earlier smoke test inferred a limit from one 429, and the Voyage docs listed the higher paid-tier numbers, so neither gave ground truth. The run did. Inferring a limit from a single observation is not the same as confirming it.

**Next:**
- Pinecone indexing: create a serverless index at 1536 dims (cosine), upsert the 584 vectors from chunks_embedded.jsonl with metadata, and verify retrieval returns the known-correct chunk for a few hand-checked queries. Indexer reads the persisted file — no re-embedding.

## 2026-05-21 — Pinecone indexing and first retrieval baseline

**Worked on:** Built the indexer (src/production_rag_forensics/retrieval/indexer.py), created the Pinecone serverless index, upserted all 584 embedded chunks, ran a first retrieval sanity check, and verified no index duplication.

**Decisions:**
- Serverless index fastapi-docs-v1, 1536 dims, cosine, AWS us-east-1 (free-tier region), created via pc.indexes.create() (current API, not the deprecated create_index shim).
- Chunk text stored in Pinecone metadata alongside the vector so retrieval returns content directly — all 584 chunks' metadata verified under Pinecone's ~40KB/vector limit (largest, stream-data, ~7KB).
- Indexer reads persisted chunks_embedded.jsonl — no re-embedding.

**Measurements:**
- 584 vectors upserted; describe_index_stats confirms exactly 584; all 584 chunk_ids unique; query 1 top-5 returned 5 distinct ids (no duplication).
- First retrieval sanity check (3 hand-picked queries, dense-only, top_k=5, no reranking): top-1/top-2 on-topic for all three. Score separation varied — tight for path-params (~0.51–0.60), sharp for error-handling (0.53→0.49→0.39). Dense-only baseline.

**What surprised me:**
- Per-query variance in score separation: dense retrieval discriminated cleanly on some query types and poorly on others (flat spread where the correct chunk barely led topically-adjacent ones, e.g. a path-prefix/proxy chunk sitting near the path-parameter-definition chunk). First empirical hint of (a) the case for reranking — does joint query-chunk scoring widen the gap? — and (b) why per-category eval is necessary, since aggregate retrieval quality hides this variance. Hypotheses to test in Phase 2, not findings.
- A query surfacing two different chunks from the same source file (two sections of path-params.md) initially looked like possible duplication; verification confirmed distinct chunk_ids — expected behavior, not a bug.

**Next:**
- LangGraph retrieval workflow (src/production_rag_forensics/orchestration/graph.py): wire query → embed → Pinecone retrieve → generation into an agent loop, then thread Langfuse tracing through it. (Hybrid sparse+dense retrieval and reranking remain queued as later retrieval-quality stages.)

## 2026-05-21 — LangGraph orchestration and prompt caching

**Worked on:** End-to-end RAG pipeline wired: embed → retrieve → generate with Claude Sonnet 4.6, Anthropic prompt caching on system prompt, Langfuse span instrumentation scaffolded.

**Decisions:**
- Anthropic SDK directly for generation, not LangChain wrappers — preserves per-stage cost as a separately measurable quantity
- cache_control on system prompt only; retrieved chunks excluded from cache — chunks rotate per query, caching them would thrash the cache and increase cost
- Committed before Langfuse traces confirmed — observability wire-up is not a correctness gate; code correctness verified independently
- Tightened pre-commit secret scan to pattern-match actual key values (sk-/pk-/Bearer + 10-char gate); dropped bare "secret"/"password" matches that flagged env var names as false positives

**Measurements:**
- Smoke test: "How do I declare path parameters in FastAPI?"
- Answer: grounded, 4 sub-topics covered, no hallucinations detected
- Retrieval: ranks 1–2 path-params.md (correct), rank 5 dependencies/index.md (weak) — dense-only, no reranker
- Cache write: 2,622 tokens on first call (cache_creation=2622, cache_read=0)
- Cache read: pending second smoke test run confirmation

**What surprised me:**
- LangGraph StateGraph adds real boilerplate overhead for a linear 3-node pipeline; abstraction does not earn its place until the graph has conditional branching

**Next:**
- Start Docker Desktop, wire Langfuse keys into .env, confirm all 3 node spans appear under parent trace
- Run test_graph.py second time to confirm cache_read=2622, cache_creation=0
- Phase 2: src/eval/harness.py — Cody writes first eval questions before harness is built
