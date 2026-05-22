# Failure Modes

Documented during Phase 3 analysis after baseline eval results and LLM-as-judge scoring
across 150 questions (30 per category: conceptual, syntactic, cross_reference, edge_case,
out_of_scope). Faithfulness scored 0–5 by Gemini 2.5 Flash; ambiguous (2–3) flagged for
manual review.

Overall: mean faithfulness 4.41. Flagged ambiguous: 22 (14.7%). Faith=0: 5 (3.3%).

---

## Failure Mode Diagnostic Map

Where each failure originates in the pipeline and what the fix surface is:

| FM  | Name                                    | Retrieval result        | Generation behavior                        | Fix surface        |
|-----|-----------------------------------------|-------------------------|--------------------------------------------|--------------------|
| FM-1 | Retrieval Miss → Confident Fabrication | Complete miss — wrong chunks returned | Model fills vacuum with fabricated detail. No uncertainty expressed. | Retrieval (hybrid search, relevance threshold) |
| FM-2 | Parametric Knowledge Leakage           | Partial hit — relevant but incomplete chunks | Model correctly uses chunks then adds content from training knowledge not present in any chunk. Grounded and injected content appear in the same answer. | Generation (anti-injection prompt instruction) |
| FM-3 | Factual Contradiction                  | Full hit — correct chunks returned | Model contradicts what the chunk explicitly states. Parametric prior overrides in-context evidence. | Generation (re-read / quote-before-claim instruction) |
| FM-4 | Multi-Document Synthesis Gap           | Partial hit — correct individual chunks, missing integration | Model correctly describes each component but cannot explain how they interact because no chunk contains the integration. | Retrieval + Corpus (reranking where integration chunk exists but ranks low; corpus augmentation where it doesn't exist at all) |

**Reading this table:**
- FM-1 and FM-4 are primarily retrieval failures. Better retrieval directly attacks the root cause.
- FM-2 and FM-3 are primarily generation failures. Better prompting directly attacks the root cause.
- FM-4 has a hard limit: reranking can only promote chunks that exist. If the integration 
  document was never written, no retrieval improvement can surface it.
- FM-1 is named "confident fabrication" not "hallucination" because the failure mechanism 
  is retrieval returning wrong chunks — the model filling the vacuum is the symptom, not 
  the cause. Hallucination implies the model invented spontaneously; here retrieval created 
  the condition for fabrication.
- FM-2 is named "parametric knowledge leakage" not "hallucination" because retrieval 
  partially worked — the model is supplementing real retrieved content with training 
  knowledge, not inventing from nothing.

---

## Optimized Stack Results (vs baseline)

Optimized stack: hybrid retrieval (BM25 + dense, RRF) + few-shot grounding prompt,
Sonnet 4.6 generation. Baseline: dense-only + short prompt. Both scored by the
identical Gemini 2.5 Flash judge (faithfulness + precision@5). Optimized run 3x
for run-to-run variance; a delta is "real" only if it exceeds the spread.

Faithfulness (all deltas real, above spread):
| Category | Baseline | Optimized | Delta |
|---|---|---|---|
| conceptual | 4.33 | 4.64 | +0.31 |
| syntactic | 4.77 | 4.90 | +0.13 |
| cross_reference | 4.60 | 4.74 | +0.14 |
| edge_case | 4.33 | 4.81 | +0.48 |
| out_of_scope | 4.83 | 4.99 | +0.16 |
| TOTAL | 4.57 | 4.82 | +0.25 |

faith=0 (confident fabrication): 4 → 0 across all three runs. The FM-1 failure
mode is eliminated in aggregate.

Flagged ambiguous (faith 2-3): 7 → 9-12. This is improvement, not regression:
faith=0 fabrications converted into faith 2-3 honest partials. The bottom of the
distribution lifted — a flagged partial answer is strictly better than a confident
hallucination.

Precision@5 (retrieval quality, mostly stable — retrieval is deterministic so
run spread is near-zero):
| Category | Baseline | Optimized | Delta | Real? |
|---|---|---|---|---|
| conceptual | 0.60 | 0.59 | -0.01 | noise |
| syntactic | 0.58 | 0.65 | +0.07 | yes |
| cross_reference | 0.59 | 0.58 | -0.01 | noise |
| edge_case | 0.47 | 0.46 | -0.00 | noise |
| out_of_scope | 0.34 | 0.15 | -0.19 | yes |

Two precision@5 findings:
- syntactic +0.07: hybrid retrieval's BM25 component helps keyword-heavy
  syntactic queries find exact-term chunks. Retrieval genuinely improved here.
- out_of_scope -0.19: hybrid retrieves MORE keyword-matched-but-irrelevant
  chunks on unanswerable questions (BM25 matches keywords in queries about
  nonexistent features). Yet faithfulness on out_of_scope ROSE to 4.99 — the
  grounding prompt makes the model refuse cleanly despite the noisier context.
  Lower retrieval precision, higher answer quality: the two metrics together
  reveal what neither shows alone. This is the diagnostic value of precision@5 —
  it caught a retrieval degradation hidden behind a rising faithfulness score.
  Implication (documented, not built): a relevance-threshold gate would prevent
  low-relevance chunks reaching generation on out-of-scope queries.

Mechanism attribution: faithfulness rose broadly while precision@5 stayed
roughly flat (except the two noted). This means the faithfulness gains came
primarily from the few-shot grounding prompt (generation-side), not from
retrieval relevance changes — consistent with the per-failure-mode findings
where grounding fixed FM-2/FM-3 and hybrid fixed FM-1's retrieval misses
specifically. The aggregate improvement is generation-led, retrieval-assisted.

---

## FM-1: Retrieval Miss → Confident Fabrication

### Symptom

The RAG system returns a detailed, confident answer with working code examples. The answer
looks like documentation. No uncertainty is expressed. The answer is entirely fabricated —
none of the code or behavioral claims appear in the retrieved chunks. The user cannot
distinguish it from a grounded answer.

### Measurement

5 records scored faith=0 by Gemini 2.5 Flash, confirmed by manual inspection of chunk
content vs. answer content:

| ID    | Category        | Question (abbreviated)                                                    |
|-------|-----------------|---------------------------------------------------------------------------|
| c3_12 | cross_reference | OAuth2PasswordBearer + API gateway prefix (tokenUrl relative URL behavior)|
| c3_20 | cross_reference | RequestValidationError + logging raw payload                              |
| c3_24 | cross_reference | Custom auth + sub-dependencies (complete dependency chain)                |
| c4_29 | edge_case       | Invalid JSON logging (two distinct implementation approaches)             |
| c5_11 | out_of_scope    | Template rendering with auth (full Jinja2 + OAuth2 implementation)        |

In all 5 cases: retrieved chunks contained zero content about the answer's core claims.
c3_12 returned chunks about path parameters and bigger-applications.md — no OAuth2 content
at all. The model answered as if the chunks had described OAuth2PasswordBearer in detail.

Faith=0 rate: 3.3% overall; 10% in cross_reference (3/30); 3.3% in edge_case (1/30);
3.3% in out_of_scope (1/30). Zero in conceptual or syntactic.

### Mechanism

Dense-only retrieval (text-embedding-3-small, cosine similarity, top_k=5) returns the
nearest embedding-space neighbors, which are topically-related but do not contain the
specific concept the question targets. The model receives a system prompt instructing it
to answer from the provided context. It does not refuse or hedge — it fabricates.

This is a composition failure: dense retrieval silently returns wrong content; the
generation model does not surface uncertainty when context is irrelevant; the combination
produces a confident hallucination indistinguishable from a grounded answer.

The current system has no relevance threshold, no retrieval-quality signal passed to the
generator, and no abstention prompt. The model is never given permission to say "the
context does not contain enough information."

### Mitigation

Mitigated via hybrid retrieval (BM25 sparse + dense, RRF-merged, k=60), combined
with the few-shot grounding prompt. BM25 keyword matching surfaces documents containing
exact query terms that dense cosine similarity misses when the embedding neighborhood
is topically adjacent but lexically wrong.

Measured on all 5 FM-1 faith=0 records (original dense baseline → hybrid + few-shot):

| ID    | Baseline | Hybrid+FS | Delta | Mechanism |
|-------|---------|-----------|-------|-----------|
| c3_12 | 0       | 5         | +5    | BM25 on "oauth2passwordbearer"/"tokenurl" surfaced security/first-steps.md, oauth2-scopes.md; dense returned path-params chunks |
| c3_20 | 0       | 5         | +5    | handling-errors.md, response-model.md recovered |
| c5_11 | 0       | 5         | +5    | templates.md surfaced; clean out-of-scope refusal from grounding prompt |
| c3_24 | 0       | 3         | +3    | sub-dependencies chunks retrieved; still flagged — synthesis gap exposed |
| c4_29 | 0       | 3         | +3    | handling-errors.md retrieved; still flagged — synthesis gap exposed |

5 of 5 improved, 0 regressions, 3 fully resolved to faith=5.

**Key finding — stacked failure modes:** c3_24 and c4_29 went 0→3, not 0→5. Their
faith=0 was FM-1 (wrong chunks → fabrication). Hybrid fixed the retrieval miss — the
correct chunks now return — but exposed FM-4 underneath: once the right chunks are
retrieved, the questions still require multi-chunk synthesis that no single chunk
contains. Hybrid retrieval does not create FM-4; it reveals FM-4 that was previously
masked by FM-1. A retrieval-miss fabrication and a synthesis gap can coexist on the
same question; fixing the first surfaces the second.

Bundled-change caveat: this compares hybrid+few-shot against the original dense
baseline, so deltas include both the grounding prompt (abstention may lift some
faith=0 via refusal-instead-of-fabrication) and hybrid retrieval. Per-record
attribution between the two was not isolated. The claim is that the combined
optimized stack resolved all 5 FM-1 records.

BM25 noise note: on simple queries dense already handles well (e.g. "path parameters"),
BM25 can surface term-frequency noise (dependencies/index.md matched on "parameters").
RRF fusion dampens this — dense's correct chunks still rank high — but it is a watch
item for the full-corpus run.

Remaining unmeasured candidates:

1. **Relevance threshold**: If max(chunk_scores) < threshold T, route to abstention path
   rather than generation. The 5 faith=0 records had top-1 scores of 0.48–0.55,
   indistinguishable from correct-retrieval scores by score alone — harder to calibrate
   than the hybrid fix.

### Generalization

Production RAG stacks systematically under-signal retrieval failure to the generator.
The retrieval layer is optimized for recall (top_k=5 nearest neighbors always returns
something); the generation layer is optimized for answer quality conditioned on context.
Neither layer is responsible for detecting the case where retrieval returned irrelevant
content — so no layer catches it. The confident hallucination is a natural emergent
behavior of this architecture, not an edge case. Any RAG stack without an explicit
abstention signal or retrieval quality gate will exhibit this failure at some non-zero rate.

---

## FM-2: Parametric Knowledge Leakage on Partial Retrieval

### Symptom

The RAG system returns an answer that is partially grounded in the retrieved chunks. The
answer correctly quotes or paraphrases chunk content in one section, then pivots to
introducing concepts, types, or implementation patterns that do not appear anywhere in the
chunks. The injected knowledge is plausible and often correct as general Python or
framework knowledge. The grounded and injected content appear in the same answer without
clear demarcation.

### Measurement

8 records scored faith=2 where the primary failure was knowledge injection (not
factual contradiction — see FM-3):

| ID    | Category        | Injected content not in chunks                              |
|-------|-----------------|-------------------------------------------------------------|
| c1_15 | conceptual      | Inferences about multi-worker lifespan event behavior       |
| c1_16 | conceptual      | Claims about RequestValidationError scope and behavior      |
| c3_08 | cross_reference | Core explanation of middleware/exception interaction        |
| c4_02 | edge_case       | StrictInt and StrictStr Pydantic types                      |
| c4_30 | edge_case       | run_in_threadpool exception propagation mechanism          |

c4_02 is the clearest case: chunks covered Union type ordering; the answer correctly
quoted this, then introduced StrictInt/StrictStr — Pydantic types not mentioned in any
retrieved chunk — as the recommended solution.

Faith=2 injection rate: 5.3% overall (8/150). Concentrated in cross_reference (3/30 = 10%)
and edge_case (3/30 = 10%).

Note: 3 additional faith=2 records are FM-3 (factual contradiction), not injection.

### Mechanism

The model has extensive parametric knowledge about FastAPI, Pydantic, and Python that
overlaps significantly with the retrieval corpus. When chunks provide partial but
insufficient context, the model blends chunk content with parametric knowledge to produce
a complete-seeming answer. The system prompt instructs "answer based on the retrieved
context" but does not prohibit adding external knowledge — it is an instruction toward
grounding, not a hard constraint against injection.

The injection is more likely when:
- The question targets a specific behavior that the retrieved chunks allude to but don't
  fully explain (c4_30: middleware + yield dependencies — chunks discuss yield dependencies
  and middleware separately, not their interaction).
- The model "knows" the answer from training and the partial context is enough to activate
  that knowledge pathway.

### Mitigation

Mitigated via few-shot grounding prompt (generation-side, single bundled change).
The system prompt was expanded from ~50 tokens to ~1,408 tokens with four worked
examples: a full grounded answer, an honest partial answer, an injection refusal
(Example 3), and a contradiction guard (Example 4). The 1,408-token prompt also
crossed Anthropic's 1,024-token cache floor, activating prompt caching as a side
effect (cache_creation=1,262 on first query, cache_read=1,262 on subsequent queries).

Measured on 5 FM-2 records (baseline → few-shot faithfulness):

| ID    | Baseline | Few-shot | Delta |
|-------|---------|---------|-------|
| c1_15 | 2       | 5       | +3    |
| c1_16 | 2       | 5       | +3    |
| c4_02 | 2       | 5       | +3    |
| c4_30 | 2       | 5       | +3    |
| c3_08 | 2       | 3       | +1 (still flagged) |

4 of 5 fully resolved (faith=5). 1 partial improvement (c3_08). 0 regressions.

c3_08 (middleware + exception headers) improved but stayed flagged — the mechanism
explanation became more grounded but remained incomplete because the chunks themselves
do not contain the full interaction chain.

Bundled-change caveat: four examples changed simultaneously; per-example attribution
would require ablation runs, not performed. The claim is that the few-shot grounding
prompt as a whole resolved FM-2 on 4 of 5 records.

Remaining candidates (not measured):

2. **Faithfulness-gated output**: Run the judge in-loop before returning the answer to
   the user. Only release answers scoring >= 4. Expensive and adds latency.

3. **Better retrieval for partial-context cases**: If injection happens when chunks are
   adjacent but not exact, improving retrieval precision might reduce the trigger
   condition. Not guaranteed — the model may still inject even with highly relevant chunks.

### Generalization

LLMs used as RAG generators cannot reliably distinguish between "I know this from my
training" and "I know this from the provided context." Instructions to stay grounded
reduce injection frequency but do not eliminate it. The injection is worst on questions
where the model's parametric knowledge overlaps with — but is more complete than — the
retrieved content. This is precisely the failure mode that LLM-as-judge scoring is
designed to catch: a human evaluator with no prior knowledge would accept the injected
answer as grounded.

---

## FM-3: Factual Contradiction Against Retrieved Content

### Symptom

The RAG system returns an answer that directly contradicts a statement present in the
retrieved chunks. The contradiction is not a gap or an inference — the chunk says X, the
answer says not-X. The user receives confidently stated incorrect information about a
topic the documentation covers correctly.

### Measurement

3 records scored faith=2 where the failure was direct contradiction of chunk content:

| ID    | Category    | Contradiction                                                              |
|-------|-------------|----------------------------------------------------------------------------|
| c1_13 | conceptual  | Answer: app-level deps run before router-level. Chunk: router deps first.  |
| c3_16 | cross_reference | Answer: response_model filtering happens before middleware. Chunks describe filtering and middleware as separate paths that interact differently.    |
| c4_30 | edge_case   | Answer attributes failure to dep exit-code running after middleware. Chunks don't establish this causal chain; the wrong mechanism is stated as fact. |

c1_13 is the sharpest case. The chunk from tutorial/bigger-applications.md explicitly
states the dependency execution order. The answer inverts it. No ambiguity in the source.

Contradiction rate: 2.0% overall (3/150). All 3 in categories with multi-chunk synthesis
requirements (conceptual, cross_reference, edge_case — 0 in syntactic or out_of_scope).

### Mechanism

Multi-chunk retrieval creates an ordering and weighting problem. The generate node receives
5 chunks concatenated in retrieval-score order. The model must synthesize across them.
In at least 2 of the 3 contradiction cases, the answer matches what the model "believes"
the correct behavior should be, not what the chunk states.

Two contributing factors:
1. **Parametric prior overrides chunk content**: The model has a prior about dependency
   execution order from training. That prior disagrees with the FastAPI implementation.
   The chunk is present in context but loses to the parametric prior.
2. **Multi-chunk confusion**: When 5 chunks cover related but distinct topics, the model
   may misattribute a claim from one chunk to the semantics of another (c3_16: response
   filtering chunk and middleware chunk cover different code paths; the answer conflates
   them).

### Mitigation

Mitigated via the same few-shot grounding prompt applied to FM-2 (contradiction-guard
Example 4 instructs the model to trust chunk content over prior knowledge and to
reflect chunk statements accurately).

Measured on 3 FM-3 records (baseline → few-shot faithfulness):

| ID    | Baseline | Few-shot | Delta | Notes |
|-------|---------|---------|-------|-------|
| c1_13 | 2       | 5       | +3    | Dependency execution order inverted: fully resolved |
| c4_30 | 2       | 5       | +3    | FM-2+FM-3 overlap: fully resolved |
| c3_16 | 2       | 2       | 0     | No change — diagnostic boundary case (see below) |

2 of 3 resolved to faith=5. 1 held at faith=2. 0 regressions.

c3_16 (response_model filtering vs. middleware) is the diagnostic boundary case: it
presents as FM-3 (the model contradicts chunk content) but the retrieved chunks are
themselves ambiguous about the response_model/middleware interaction. No generation-side
instruction can resolve a contradiction when the evidence is genuinely unclear in the
chunks. c3_16 is effectively FM-4 (the integration is not cleanly stated in any chunk)
presenting as FM-3. The two records with unambiguous chunk evidence both resolved to
faith=5; the one with ambiguous chunk evidence did not move. This confirms the fix
surface: contradiction is fixable by prompting only when the correct answer is
unambiguously present in the chunks.

Remaining candidates (not measured):

2. **Higher-confidence threshold + abstention**: If the model scores 2-3 on the judge,
   re-generate with a stricter instruction. Circular but measurable.

3. **Retrieval quality gate**: Contradictions happen when multiple chunks on related-but-
   distinct topics are retrieved together. More precise retrieval (top_k=3 with reranking)
   might reduce the context window noise that enables cross-chunk confusion.

### Generalization

LLM generation does not privilege retrieved context over parametric knowledge when they
conflict. The model is not applying the reasoning "this chunk is evidence I should trust
over my prior." It is applying the reasoning "this is what FastAPI does" — which may or
may not match the chunk. The RAG architecture assumes retrieval plus instruction equals
grounding; the contradiction evidence shows the instruction is necessary but not
sufficient. This failure mode is particularly dangerous because the model is most likely
to contradict chunks on topics where its training data contains conflicting or incorrect
information about a specific framework's behavior.

---

## FM-4: Multi-Document Synthesis Gap

### Symptom

The RAG system returns an answer acknowledging it cannot fully address the question, often
citing that the context doesn't cover the specific interaction. The retrieved chunks are
individually correct and topically relevant, but no single chunk (or combination of chunks
as retrieved) provides the integration point needed to answer a cross-document question.
The user receives a partial answer that identifies the right components but cannot explain
how they interact.

### Measurement

14 records scored faith=3 (correct but incomplete) where the primary limitation was
retrieval architecture, not generation quality:

| Category        | Faith=3 count | Faith=3 records                                      |
|-----------------|---------------|------------------------------------------------------|
| cross_reference | 5             | c3_06, c3_13, c3_17, c3_22, c3_26                   |
| edge_case       | 5             | c4_04, c4_17, c4_19, c4_22, c4_26                   |
| conceptual      | 2             | c1_08, c1_27                                         |
| syntactic       | 2             | c2_24, c2_25, c2_29                                  |

Representative example — c3_06 (UploadFile + background task file closure):
- Chunks: request-files.md (3x), background-tasks.md, stream-data.md
- Answer correctly identifies SpooledTemporaryFile from chunks and the closure problem
- Proposed solution (read contents before background task dispatch) is an inference from
  the problem, not a statement in any chunk
- Faith=3: grounded on the diagnosis, inference on the fix

Representative example — c3_22 (lifespan state → sub-dependency):
- Chunks: bigger-applications.md (3x), sub-dependencies.md, events.md
- Each chunk covers one component; no chunk describes the app.state → dependency
  parameter pattern as an integration
- Answer correctly identifies the gap and extracts what each chunk does cover
- Faith=3: honest about the limit, but unhelpful to the user

Faith=3 rate: 9.3% overall (14/150). Cross_reference 16.7% (5/30) and edge_case 16.7%
(5/30) — both substantially above the overall rate. Conceptual 6.7% (2/30). Syntactic
10% (3/30). Out_of_scope 0%.

Mean faithfulness: cross_reference 3.90 (lowest category) vs. out_of_scope 4.83 (highest).
The 0.93-point gap is primarily driven by this failure mode.

### Mechanism

Dense retrieval returns the top_k most similar individual chunks by cosine distance.
For single-concept questions (syntactic: "how do I declare a path parameter?"), the
correct chunk is top-1 with high score separation. For multi-concept questions (cross_
reference: "how does lifespan state interact with sub-dependencies?"), the retrieval
returns the best individual-topic matches — lifespan.md, sub-dependencies.md — but no
chunk contains the integration.

The documentation corpus has this integration gap by design: docs are organized by
feature, not by interaction pattern. "Using feature A with feature B" is rarely a
first-class documentation topic. The retrieval system returns the feature-A chunk and
the feature-B chunk, but the how-they-interact chunk does not exist.

Dense-only retrieval cannot compensate for a documentation gap. The embedding space
proximity of A-chunks and B-chunks does not produce an A+B answer.

### Mitigation

Three retrieval configurations measured on cross_reference (30 questions):

| Config | Mean Faithfulness | Mean Latency | Faith=0 |
|---|---|---|---|
| Dense-only (baseline) | 3.90 | 10,601ms | 3 |
| Haiku LLM reranker (pool=20, no cap) | 3.87 | 39,444ms | 3 |
| Cross-encoder + diversity cap + pool=50 | 3.70 | 13,934ms | 4 |

Dense-only wins. Both rerankers are neutral-to-negative.

Per-question asymmetry (cross-encoder vs baseline):
- CE helped 6 records — all were retrieval misses or partial hits at baseline
  (dense was already struggling; reranking recovered content, e.g. c3_12 0→5,
  c3_24 0→5)
- CE hurt 8 records — 7 of 8 were clean baseline answers (faith 4–5) that the
  cross-encoder degraded by promoting higher-relevance-scored chunks that
  carried less of the specific context the answer needed

Mechanism: Both rerankers score each chunk independently against the query
(pointwise relevance). Neither models "does this set of 5 chunks together
answer the question?" On questions where dense cosine ordering already
assembled a complete set, pointwise reranking breaks the assembly. On
questions where dense missed, reranking can recover. Net effect is negative
because there are more clean answers to break than misses to rescue.

This is knowledge fragmentation: the integration content for multi-document
synthesis questions does not exist as a retrievable unit in the corpus.
Reranking on any signal cannot surface what does not exist. The binding
constraint is corpus structure, not retrieval signal quality.

Production solutions, none of which are reranking:
1. **Cross-document chunking at index time** (CDTA-style) — synthesize
   integration content into unified chunks before indexing
2. **GraphRAG** — model entity relationships explicitly for multi-hop traversal
3. **Agentic re-retrieval** — orchestration-layer loop that judges sufficiency
   and re-retrieves with reformulated queries (this is the in-stack future
   experiment; LangGraph conditional branching would finally earn its place here)

All three are out of scope for this project and documented as the path forward
for the production-scale autopsy.

**Generation-side note (few-shot framing, 2026-05-22):** Three FM-4 records were also
tested with the few-shot grounding prompt (Example 2, honest partial answer):

| ID    | Baseline | Few-shot | Delta |
|-------|---------|---------|-------|
| c3_06 | 3       | 5       | +2    |
| c3_22 | 3       | 5       | +2    |
| c3_26 | 3       | 5       | +2    |

All three moved from faith=3 to faith=5. This does not contradict the reranking
finding. These records had adequate retrieval — the correct individual chunks were
returned at baseline — but the generation under-used them, producing "correct but
incomplete" answers. The few-shot framing example (Example 2) changed the generation
behavior: the model now explicitly states what the chunks do and do not cover rather
than under-delivering. This improved the judge score because the answer became more
honest and complete within the limits of the retrieved evidence.

FM-4 records where the integration content is genuinely absent from the corpus remain
unfixable by prompting. The generation fix applies only to records where retrieval
succeeded but the generator failed to extract full value from the chunks. These are
two distinct sub-cases of FM-4 that the few-shot experiment separated empirically.

### Generalization

Documentation corpora organized around features rather than interactions are structurally
inadequate for production cross-reference queries. This is the default organization for
every major framework's documentation. Dense retrieval over this corpus structure cannot
answer "how does X interact with Y?" because the corpus does not contain the answer as
a retrievable unit. The failure mode is architectural, not a retrieval quality problem:
better retrieval over the same corpus returns better individual-feature chunks, not the
missing integration documentation. Production RAG stacks targeting developer documentation
will reliably surface this failure in cross-reference and multi-step workflow query classes.
The mitigation requires either augmenting the corpus (synthetic integration documents) or
improving the generator's ability to synthesize across partial evidence — neither of which
is a standard retrieval optimization. Iterative/agentic retrieval with a validated
sufficiency gate is the orchestration-layer answer to knowledge fragmentation; a prior
implementation of this pattern (Pydantic-validated re-retrieval) exists in the aether
project.
