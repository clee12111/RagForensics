# Production RAG Stack Forensics

A measured study of where production RAG breaks. Built on the industry-standard stack — LangGraph, Pinecone, Langfuse, and frontier LLM APIs — applied to the FastAPI documentation corpus, with instrumentation exposed as a Model Context Protocol (MCP) server.

**Status:** In active development. Autopsy publishing as failure modes are characterized.

## Stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph |
| Vector DB | Pinecone (managed) |
| Observability | Langfuse |
| Inference | Claude Sonnet 4.6 (primary), GPT-5.4 + Gemini 3.1 Pro (cross-provider study), Claude Haiku 4.5 (reranking) |
| Service layer | FastAPI |
| Agent interface | Custom MCP server |
| Corpus | FastAPI documentation |

## Architecture

```
                           USER QUERY
                                │
                                ▼
                  ┌──────────────────────────────┐
                  │   FastAPI service layer      │
                  └──────────────┬───────────────┘
                                 ▼
                  ┌──────────────────────────────┐
                  │   LangGraph agent loop       │
                  │   (retrieval → rerank → gen) │
                  └──┬──────────────┬───────────┬┘
                     │              │           │
            ┌────────▼──────┐  ┌────▼──────┐  ┌─▼──────────────┐
            │   Pinecone    │  │  Haiku    │  │  Sonnet 4.6    │
            │   hybrid      │  │  reranker │  │  (+ GPT-5.4,   │
            │   retrieval   │  │           │  │   Gemini 3.1)  │
            └───────────────┘  └───────────┘  └────────────────┘
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
```

## What it is

A forensic engineering artifact applied to production AI infrastructure. Each documented failure mode follows a five-section template: symptom, measurement, mechanism, mitigation, generalization. The deliverable is the autopsy, not the demo.

This is a continuation of the methodology applied to my [polymarket-autopsy](https://github.com/clee12111/polymarket-autopsy), now turned on the production stack that AI engineering teams actually ship on.

## What it is not

- Not a tutorial. The autopsy assumes the reader understands RAG basics.
- Not a benchmark. No claim that one provider "wins." Tradeoffs are documented with measurements.
- Not a LangGraph promotion piece. Where the framework helps, the autopsy says so. Where it gets in the way, the autopsy says so with measurements.

## Related work

- [polymarket-autopsy](https://github.com/clee12111/polymarket-autopsy) — same methodology applied to a custom trading system.
- [aether](https://github.com/clee12111/aether) — retrieval system built bottom-up (ChromaDB local, direct API calls). This project is the production-tools counterpart.

## Author

Cody Lee · [codylee.tech](https://codylee.tech) · [github.com/clee12111](https://github.com/clee12111)
