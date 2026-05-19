# CLAUDE.md

Instructions for Claude Code when working in this repo.

## Project context

This is **Production RAG Stack Forensics** — a forensic engineering study of a production RAG system built on LangGraph, Pinecone, Langfuse, and frontier LLM APIs over the FastAPI documentation corpus.

The deliverable is the **engineering record** in `docs/`:
- `docs/journal.md` — chronological engineering journal, updated every work session
- `docs/failure_modes.md` — 3–5 documented failure modes with 5-section template each
- `docs/architecture.md` — locked design decisions snapshot

This is not a post-mortem. It is the real-time engineering record of how a production-grade RAG system gets built, with the judgment documented as it happens.

The internal blueprint with full execution detail is in `PLAN.md` (local only, on Cody's desktop). Reference it when needed.

## Core working principles

1. **The engineering record is the deliverable.** Code exists to surface and instrument decisions, measurements, and failure modes. Polish is not the goal; documented judgment is.

2. **The journal must be updated at the end of every work session.** Before stopping, write the entry in `docs/journal.md` using the template below. Claude Code should prompt for the journal entry if it hasn't been written.

3. **Brutal honesty over capitulation.** When the user pushes back, evaluate on merit. If you're wrong, correct. If you're right, hold the position and explain with evidence. Don't soften under fatigue or pressure.

4. **Verify before trusting.** Test claims with measurements, not assertions. Show numbers.

5. **Single-axis changes.** When investigating a failure mode, change one variable at a time.

6. **Per-component tables, not verbal aggregations.** Never report "the system performed well overall." Always produce a per-category breakdown.

## Journal entry template

```markdown
## YYYY-MM-DD — short title

**Worked on:** one-sentence summary

**Decisions:**
- Decision 1 (with one-line rationale)
- Decision 2 (with one-line rationale)

**Measurements:**
- Metric 1: value (context)
- Metric 2: value (context)

**What surprised me:**
- Brief note on anything unexpected

**Next:**
- Specific next step
```

Discipline: entries are factual, not narrative. Don't editorialize. Don't write about strategic context. Don't write about hours spent or frustration.

## Failure mode template

Each entry in `docs/failure_modes.md` follows the 5-section structure:

1. **Symptom** — user-facing behavior that surfaces the bug
2. **Measurement** — how detected, size of effect, actual numbers
3. **Mechanism** — why it happens in the stack as built
4. **Mitigation** — what was tried, what worked, what didn't
5. **Generalization** — what this says about production RAG stacks broadly

Target 3–5 well-documented failure modes. Quality over quantity.

## Scope discipline

Out-of-scope items — do not propose adding these without explicit user request:

- Self-hosted inference (vLLM, Ollama)
- Multi vector DB ablation (Pinecone is the choice)
- RAGAS or external eval frameworks (custom harness only)
- Parallel raw-SDK implementation
- Kubernetes (Docker Compose is sufficient)
- Fine-tuning, multimodal, or UI polish

If the user proposes adding any of these, push back. Reference this list.

## Stack (locked)

| Layer | Choice |
|---|---|
| Orchestration | LangGraph |
| Vector DB | Pinecone (managed) |
| Observability | Langfuse (self-hosted via Docker) |
| Inference (primary) | Claude Sonnet 4.6 with prompt caching |
| Inference (comparison) | GPT-5.4, Gemini 3.1 Pro |
| Reranking | Claude Haiku 4.5 |
| Service layer | FastAPI |
| Agent interface | Custom MCP server (Streamable HTTP transport) |
| Corpus | FastAPI documentation |

## Commit message discipline

Commit messages are permanent and public. They are not the journal — the journal lives in `docs/journal.md`.

- Verb-first imperative: "Add X", "Fix Y", "Refactor Z"
- ~50 characters max for summary line
- States what changed, not why or how
- No mention of failures, struggles, or hours spent
- No mention of strategic context (applications, target firms)
- Body (optional) describes scope, not journey

Before committing, ask: "If a hiring manager at a target company read this commit message in 2 years, would it look professional?"

## Safety checks (before any commit or push)

Run these checks before every commit. Refuse to commit if any fail.

### Pre-commit

1. **No secrets in staged files.** Grep for `sk-`, `pk-`, `Bearer `, `password`, `secret`. Stop if anything matches.
2. **No large data files.** Anything over 1MB in `data/corpus/` should be gitignored.
3. **No personal context in committed files.** No mention of target firms, application strategy, or other private content.
4. **No half-finished journal entries.** If `docs/journal.md` was modified, the latest entry must have all 5 fields filled in.
5. **No measurements without source.** Numbers cited in `docs/` must be reproducible from an eval run, trace, or script.

### Pre-push

1. Run pre-commit checks on all commits in the push range.
2. Confirm branch is `main` and remote URL is correct (`git remote -v`).
3. Confirm working tree is clean.
4. Confirm `git log --oneline -5` matches expectations.

### When a check fails

Stop. Fix locally. Do not push. Force-push is reserved for emergencies (committed secret recovery) and requires explicit user confirmation.

## File structure conventions

- `src/` modules have single responsibility, two levels deep maximum
- Tests not required — this is a research artifact, not a maintained library
- Configs go in `pyproject.toml` and `.env`, not separate config files
- The three deliverables in `docs/` are the single source of truth

See PLAN.md for the full directory tree.

## Build order

Build files in the order specified in PLAN.md § "File-by-file build order." Do not skip ahead.

## Eval methodology

- 100 questions, stratified across 5 categories (20 each): conceptual, syntactic, cross-reference, edge-case, out-of-scope
- Questions hand-written by the user from real FastAPI usage. Do NOT generate eval questions.
- Metrics: faithfulness (5-point manual), precision@5, latency p50/p95, cost per query by stage
- Always report per-category breakdown. Never aggregate-only.

## Cost discipline

Project budget envelope is ~$80, hard ceiling $150. Before running anything that would cost more than $20 in a single execution, stop and confirm with the user.

## Communication style

- Code blocks for runnable commands and full file contents
- Prose for analysis and reasoning
- When uncertain, propose a measurement that would resolve the uncertainty
- End sessions by writing the journal entry, then clearly stating what's next

## Things to flag immediately

Stop and tell the user before proceeding if:

- A failure mode has been "investigated" but you cannot produce per-category measurements
- The same architectural choice keeps coming back for debate (signals indecision, not new evidence)
- More than 3 unrelated changes are being made in one session
- The user is fatigued and proposing scope expansion
- A proposed change would invalidate previous measurements in `docs/failure_modes.md`
- The journal hasn't been updated in more than 24 hours of project activity

## Reference repos

- `polymarket-autopsy` — methodology applied to a custom trading system. Reference for the 5-section template.
- `aether` — bottom-up retrieval primitives. Reference for chunking, hybrid search, RRF patterns. This repo is the production-tools counterpart.