# MCP Server Demo

The project's instrumentation is exposed as six agent-queryable tools over the
Model Context Protocol, spanning three data sources: the engineering docs, the
evaluation results, and live Langfuse traces. Below are real exchanges with the
server connected to Claude Desktop — each query hits a different tool and data
source, showing the agent selecting the right tool and answering from the
project's own measurements.

> Connected via stdio transport. Each exchange shows the `Used rag-forensics
> integration` tool call. Answers are unedited except where noted.

---

## 1. Live trace data — retrieval latency

**Tool:** `retrieval_latency` (reads Langfuse spans)

> **Q:** What's the p50 and p95 retrieval latency?

Retrieval latency across the last 200 spans:

| Metric | Latency |
|---|---|
| p50 | 87 ms |
| p95 | 578 ms |
| Mean | 233 ms |
| Min | 81 ms |
| Max | 872 ms |

The p50 is healthy; the p95 (578 ms) is ~6.6x the median, indicating a fat tail
on a minority of retrievals. *(The agent speculated on causes — cold starts,
large payloads — but those were not measured; noted here as hypotheses, not
findings.)*

---

## 2. Engineering record — a failure mode

**Tool:** `failure_modes` (reads `docs/failure_modes.md`)

> **Q:** Show me failure mode 1.

**FM-1: Retrieval Miss -> Confident Fabrication**

The core problem: dense-only retrieval (`text-embedding-3-small`, cosine,
top_k=5) returns topically-adjacent chunks that don't actually contain the
answer. The model receives no signal that the context is irrelevant — no
relevance threshold, no abstention prompt — so it fabricates a confident,
documentation-quality answer indistinguishable from a grounded one.

**Scale:** 5 records scored faith=0, concentrated in `cross_reference` (10% hit
rate, 3/30).

**The fix:** Hybrid retrieval (BM25 sparse + dense, RRF-merged) + few-shot
grounding prompt. BM25 keyword matching catches cases where the dense embedding
neighborhood is topically right but lexically wrong — e.g. on `c3_12`, dense
returned path-params chunks for an `OAuth2PasswordBearer` question, while BM25
on the exact token surfaced the correct security docs.

**Results:** 5/5 FM-1 records improved, 0 regressions, 3 fully resolved to
faith=5.

**The wrinkle:** `c3_24` and `c4_29` only went 0->3, not 0->5. Fixing the
retrieval miss exposed FM-4 (multi-chunk synthesis gap) underneath — the right
chunks now return, but the answer requires synthesizing across them in a way no
single chunk contains. FM-1 was masking FM-4 on those two.

*(This exchange returns the committed record accurately — a good demonstration
that the agent reads the real `docs/`, not its own priors.)*

---

## 3. Per-stage cost

**Tool:** `cost_per_query_stage` (reads eval JSONL)

> **Q:** What's the cost per query broken down by pipeline stage?

Cost breakdown for `optimized_run1` across 150 queries:

| Stage | Mean / query | Total |
|---|---|---|
| Generate | $0.00556 | $0.834 |
| Judge | $0.000123 | $0.018 |
| **Total** | **$0.00569** | **$0.853** |

Generation dominates (~98% of cost); the judge (Gemini 2.5 Flash) is
effectively free. Generation latency is high (p50 ~9.9 s), worth watching for
any interactive use case.

> **Note on the numbers:** this is the gen + judge cost of a *single optimized
> run* (Sonnet generation + Gemini judge). It is a different figure from the
> per-provider generation costs in the cross-provider study ($2.07 / $5.06 /
> $0.18 per 150 q), which measure full-run generation cost per provider with the
> judge held constant. The two are not in conflict — they measure different
> things.

---

## 4. Eval results — cross-provider cost vs. faithfulness

**Tool:** `eval_results` (reads eval JSONL)

> **Q:** What did the cross-provider study find on cost versus faithfulness?

| Provider | Model | Faithfulness | Cost / query | Latency p50 |
|---|---|---|---|---|
| Google | gemini-3.1-flash-lite | 4.62 | $0.0012 | 2.6 s |
| Anthropic | claude-sonnet-4-6 | 4.45 | $0.0138 | 10.1 s |
| OpenAI | gpt-5.5 | 4.39 | $0.0337 | 11.1 s |

Faithfulness spans 0.23 points across a ~28x cost range. When retrieval and
grounding are strong, generation-model capability is not the bottleneck — a
small, cheap model reaches the same faithfulness as flagships costing an order
of magnitude more.

> **A correction to the agent's own answer.** In the live session, the agent
> also reported per-provider **precision@5** (0.555 / 0.577 / 0.682) and read it
> as a retrieval-quality finding — *"OpenAI's retrieved chunks are more
> relevant."* **That is incorrect, and it's the exact artifact documented in the
> eval-harness failure mode.** Retrieval is deterministic and identical across
> all three providers: the same query returns the same chunks regardless of
> which model generates the answer. So a cross-provider P@5 difference is
> impossible as a real retrieval signal — it's a judge artifact (the judge
> reading the *answer* to score chunk relevance). The honest cross-provider
> finding is faithfulness across cost, with P@5 excluded. The agent confidently
> surfaced the trap the autopsy documents — which is exactly why the autopsy,
> not the agent's live read, is the system of record.

This caveat is the point of the exercise. An agent querying the instrumentation
can produce a plausible, confident, and wrong interpretation of a contaminated
metric. Catching it requires knowing the system's failure modes — which is what
the forensic record is for.

---

*The full unedited session is public here:
[live MCP session](https://claude.ai/share/b865167c-b27f-4a3e-8ec3-60cef85b4add).
The cross-provider precision@5 misread flagged in exchange 4 appears there as
the agent originally produced it — preserved deliberately, since catching it is
the point.*

---

## What this demonstrates

Four queries, three distinct data backends — live Langfuse traces, the
committed engineering docs, and the evaluation results — with the agent
selecting the correct tool each time. The server closes a recursive loop: an AI
agent interrogating the forensic record of an AI system through the same kind of
interface a production system would expose. It also demonstrates the limit of
that loop: the agent can read the metrics, but interpreting them correctly still
depends on the documented failure modes (see exchange 4).

**Setup:** see the [README](../README.md) for the Claude Desktop / stdio
configuration. The server also supports Streamable HTTP transport for remote
clients.