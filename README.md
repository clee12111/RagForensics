# Production RAG Stack Forensics

A measured study of where production RAG breaks. Built on the industry-standard 
stack — LangGraph, Pinecone, Langfuse, and frontier LLM APIs — applied to the 
FastAPI documentation corpus, with instrumentation exposed as a Model Context 
Protocol (MCP) server.

**Status:** In active development. Four failure modes characterized and 
mitigated with measurements; cross-provider study and MCP server in progress.

## Stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph |
| Vector DB | Pinecone (managed, serverless) |
| Retrieval | Hybrid — dense (OpenAI text-embedding-3-small) + BM25 sparse, RRF-merged |
| Observability | Langfuse |
| Generation | Claude Sonnet 4.6 (primary), GPT-5.5 + Gemini 3.1 Pro (cross-provider study) |
| Judge | Gemini 2.5 Flash (LLM-as-judge, independent of generation) |
| Service layer | FastAPI |
| Agent interface | Custom MCP server |
| Corpus | FastAPI documentation (584 chunks, include-directives resolved) |

## Architecture                       USER QUERY
                            │
                            ▼
              ┌──────────────────────────────┐
              │   FastAPI service layer      │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │   LangGraph pipeline         │
              │   (retrieve → generate)      │
              └──┬───────────────────────┬───┘
                 │                       │
        ┌────────▼──────────┐    ┌───────▼────────────┐
        │  Hybrid retrieval │    │  Sonnet 4.6        │
        │  dense + BM25     │    │  (+ GPT-5.5,       │
        │  RRF-merged       │    │   Gemini 3.1 Pro)  │
        └───────────────────┘    └────────────────────┘
                             │
                             │  every stage emits traces
                             ▼
              ┌──────────────────────────────┐
              │   Langfuse                   │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │   Custom MCP server          │
              │   (queryable autopsy tools)  │
              └──────────────────────────────┘

## Documented failure modes

Four failure modes, each with the five-section template (symptom, measurement, 
mechanism, mitigation, generalization):

- **FM-1 — Retrieval miss → confident fabrication.** Dense retrieval returns 
  wrong chunks; the model fabricates a grounded-looking answer. Mitigated by 
  hybrid retrieval (BM25 surfaces exact-term documents dense misses).
- **FM-2 — Parametric knowledge leakage.** Model supplements partial context 
  with training knowledge not in any chunk. Mitigated by a few-shot grounding 
  prompt.
- **FM-3 — Factual contradiction.** Model contradicts what a retrieved chunk 
  explicitly states. Mitigated (partially) by the same grounding prompt.
- **FM-4 — Multi-document synthesis gap.** No single chunk contains the 
  integration the question needs. Corpus-structural — three reranking 
  configurations (Haiku LLM, cross-encoder, diversity-capped) all measured 
  neutral-to-negative; the failure is upstream of retrieval ranking.

## Headline result

Optimized stack (hybrid retrieval + few-shot grounding prompt) vs dense-only 
baseline, 150 questions, 3 runs for variance, scored by an independent LLM judge 
on faithfulness and precision@5:

- Confident fabrications (faithfulness=0): **4 → 0**
- Mean faithfulness: **4.57 → 4.82** (all per-category deltas above run-to-run spread)
- Improvement is generation-led (grounding prompt), retrieval-assisted (hybrid) — 
  precision@5 stayed roughly flat while faithfulness rose, localizing the gains 
  to generation

A negative result is documented as carefully as the positive ones: reranking did 
not earn its place on this corpus, and the measurements show why.

## What it is

A forensic engineering artifact applied to production AI infrastructure. Each 
failure mode is characterized with measurements, not assertions. The deliverable 
is the autopsy, not the demo.

Continuation of the methodology from my 
[polymarket-autopsy](https://github.com/clee12111/polymarket-autopsy), turned on 
the production stack that AI engineering teams actually ship on.

## What it is not

- Not a tutorial. Assumes the reader understands RAG basics.
- Not a benchmark. No claim that one provider "wins." Tradeoffs documented with measurements.
- Not a framework promotion piece. Where LangGraph helps, the record says so; 
  where it adds overhead without payoff, the record says that too, with measurements.

## Related work

- [polymarket-autopsy](https://github.com/clee12111/polymarket-autopsy) — same methodology, custom trading system.
- [aether](https://github.com/clee12111/aether) — retrieval system built bottom-up (ChromaDB, direct API calls). This project is the production-tools counterpart.

## Author

Cody Lee · [codylee.tech](https://codylee.tech) · [github.com/clee12111](https://github.com/clee12111)
