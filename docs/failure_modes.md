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

Not yet mitigated. Mitigation candidates ranked by likely impact:

1. **Relevance threshold**: If max(chunk_scores) < threshold T, route to abstention path
   rather than generation. Requires calibrating T against the score distribution — the 5
   faith=0 records had top-1 scores of 0.48–0.55, indistinguishable from correct-retrieval
   scores by score alone.

2. **Abstention instruction in system prompt**: Add explicit instruction to say "the
   provided context does not contain enough information to answer this question" when chunks
   do not address the question. Tests whether generation-side instruction alone reduces
   hallucination rate without retrieval changes.

3. **Hybrid retrieval (sparse + dense)**: BM25 keyword match + dense cosine. A query
   for "OAuth2PasswordBearer + tokenUrl" would keyword-match the OAuth2 docs directly;
   dense-only returns path-params.md because the embedding is semantically adjacent.

None of these have been measured yet.

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

Not yet mitigated. Candidates:

1. **Explicit anti-injection instruction**: Add to system prompt: "Do not introduce
   concepts, types, or code that are not present in the provided chunks. If the chunks
   do not fully answer the question, state what is and is not covered." Tests whether
   generation-side instruction alone changes injection rate.

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

Not yet mitigated. Candidates:

1. **Re-read instruction**: Instruct the model to quote the relevant chunk sentence before
   making a claim about it. Forces explicit grounding, makes contradictions less likely.

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

Not yet measured. Ranked candidates:

1. **Reranking with joint query-chunk scoring**: A reranker (Claude Haiku 4.5, as
   planned in the stack) scores each chunk against the full query including the integration
   requirement. A reranker may deprioritize A-only chunks when the query asks about A+B,
   surfacing a chunk that mentions both even if it scores lower on dense similarity.
   Expected to help for cases where a joint-mention chunk exists but ranks outside top_k=5.

2. **Query decomposition before retrieval**: Decompose "how does X interact with Y?" into
   sub-queries ["describe X behavior", "describe Y behavior", "X Y interaction"]. Run
   retrieval on each, merge via RRF. Increases retrieval breadth; still does not create
   integration content if it doesn't exist in the corpus.

3. **Hybrid retrieval (sparse + dense)**: BM25 keyword co-occurrence may surface chunks
   that contain both terms. Less likely to help for cross-reference questions than for
   terminology-specific syntactic questions, but no measurement yet.

4. **Abstention instruction for integration gaps**: Instruct the model to distinguish
   between "I have partial evidence and can synthesize" vs. "the context requires
   information that is not present in any retrieved chunk." Likely increases faith=3→4
   transition on cases where the model is already honest; does not add content.

Haiku reranking is queued as the next retrieval improvement in the build order.

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
is a standard retrieval optimization.
