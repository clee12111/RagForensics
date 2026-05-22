"""
Eval harness: runs the 150-question eval set through the RAG pipeline.

Usage (via scripts/run_eval.py):
    python scripts/run_eval.py                         # full run
    python scripts/run_eval.py --category conceptual   # single category
    python scripts/run_eval.py --limit 5               # first N questions
    python scripts/run_eval.py --output /tmp/out.jsonl # override output path

Output: one JSON record per line in data/eval_results.jsonl.
precision_at_5 and faithfulness are null at write time — filled in manually.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

# ── Pricing constants — Sonnet 4.6 ───────────────────────────────────────────

_INPUT_PER_M   = 3.00    # $/M input tokens (cache miss)
_OUTPUT_PER_M  = 15.00   # $/M output tokens
_READ_PER_M    = 0.30    # $/M cache-read tokens (cache hit)

EVAL_SET_PATH    = Path("data/eval_set.jsonl")
DEFAULT_OUT_PATH = Path("data/eval_results.jsonl")

VALID_CATEGORIES = {
    "conceptual",
    "syntactic",
    "cross_reference",
    "edge_case",
    "out_of_scope",
}

# Short alias → full category name
_CATEGORY_ALIASES: dict[str, str] = {
    "c1": "conceptual",
    "c2": "syntactic",
    "c3": "cross_reference",
    "c4": "edge_case",
    "c5": "out_of_scope",
}


def _resolve_category(raw: str) -> str:
    """Accept full category name or short alias (c1–c5)."""
    resolved = _CATEGORY_ALIASES.get(raw.lower(), raw.lower())
    if resolved not in VALID_CATEGORIES:
        raise ValueError(
            f"Unknown category '{raw}'. Valid: {sorted(VALID_CATEGORIES)} or c1–c5."
        )
    return resolved


def _compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
) -> float:
    """
    cache hit  (cache_read_tokens > 0):
        (cache_read_tokens * READ_PER_M + output_tokens * OUTPUT_PER_M) / 1_000_000
    cache miss (cache_read_tokens == 0):
        (input_tokens * INPUT_PER_M + output_tokens * OUTPUT_PER_M) / 1_000_000

    cache_creation_tokens is not in the user-specified formula — excluded.
    """
    if cache_read_tokens > 0:
        return (cache_read_tokens * _READ_PER_M + output_tokens * _OUTPUT_PER_M) / 1_000_000
    return (input_tokens * _INPUT_PER_M + output_tokens * _OUTPUT_PER_M) / 1_000_000


def _load_questions(
    category: str | None,
    limit: int | None,
) -> list[dict]:
    questions = []
    with EVAL_SET_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            if category and q["category"] != category:
                continue
            questions.append(q)
            if limit and len(questions) >= limit:
                break
    return questions


def _print_summary(results: list[dict]) -> None:
    from collections import defaultdict

    cat_latencies: dict[str, list[float]] = defaultdict(list)
    cat_costs:     dict[str, float]       = defaultdict(float)

    for r in results:
        cat_latencies[r["category"]].append(r["latency_ms"])
        cat_costs[r["category"]] += r["cost_usd"]

    total_cost = sum(cat_costs.values())

    header = f"{'Category':<20} {'Count':>5}  {'Mean lat (ms)':>13}  {'Cost (USD)':>10}"
    print("\n" + header)
    print("-" * len(header))

    for cat in sorted(cat_latencies):
        lats  = cat_latencies[cat]
        mean  = sum(lats) / len(lats)
        cost  = cat_costs[cat]
        print(f"{cat:<20} {len(lats):>5}  {mean:>13.0f}  {cost:>10.5f}")

    print("-" * len(header))
    all_lats = [r["latency_ms"] for r in results]
    overall_mean = sum(all_lats) / len(all_lats) if all_lats else 0
    print(f"{'TOTAL':<20} {len(results):>5}  {overall_mean:>13.0f}  {total_cost:>10.5f}")


_529_WAITS = [5, 10, 20]  # seconds; fail loud after 3 retries (4th attempt)


def _run_with_529_retry(run_query, question: str) -> dict:
    """
    Call run_query(question), retrying up to 3 times on Anthropic 529 overloaded.
    Waits: 5s, 10s, 20s. Any other exception propagates immediately.
    """
    for attempt, wait in enumerate(_529_WAITS, start=1):
        try:
            return run_query(question)
        except anthropic.APIStatusError as exc:
            if exc.status_code != 529:
                raise
            print(f"  529 overloaded -- retry {attempt}/3 in {wait}s")
            time.sleep(wait)
    # Final attempt — let any exception propagate
    return run_query(question)


def _load_completed_ids(output: Path) -> set[str]:
    """Read already-completed question IDs from an existing output file."""
    if not output.exists():
        return set()
    completed: set[str] = set()
    with output.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                completed.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return completed


def run(
    category: str | None = None,
    limit:    int | None  = None,
    output:   Path        = DEFAULT_OUT_PATH,
    fresh:    bool        = False,
) -> None:
    # Import here so module-level client init only happens when actually running
    from production_rag_forensics.orchestration.graph import run_query

    questions = _load_questions(category, limit)
    if not questions:
        print("No questions matched the filter.", file=sys.stderr)
        sys.exit(1)

    output.parent.mkdir(parents=True, exist_ok=True)

    # ── Resume logic ──────────────────────────────────────────────────────────
    if fresh and output.exists():
        output.unlink()

    completed_ids = _load_completed_ids(output)
    if completed_ids:
        print(f"Resuming -- {len(completed_ids)} questions already completed, skipping")
    else:
        print("Starting fresh run")

    pending = [q for q in questions if q["id"] not in completed_ids]
    if not pending:
        print("All questions in this run already completed. Nothing to do.")
        return

    results: list[dict] = []

    with output.open("a", encoding="utf-8") as out_f:
        for q in pending:
            t0 = time.perf_counter()
            result = _run_with_529_retry(run_query, q["question"])
            latency_ms = round((time.perf_counter() - t0) * 1000)

            cache_read = result["cache_read_tokens"]
            cache_cre  = result["cache_creation_tokens"]
            inp        = result["input_tokens"]
            out_tok    = result["output_tokens"]

            cost = _compute_cost(inp, out_tok, cache_cre, cache_read)

            chunks_out = [
                {
                    "source_file": c["source_file"],
                    "score":       round(c["score"], 4),
                    "chunk_index": i,
                }
                for i, c in enumerate(result["chunks"])
            ]

            record: dict = {
                "id":                    q["id"],
                "category":              q["category"],
                "question":              q["question"],
                "answer":                result["answer"],
                "chunks":                chunks_out,
                "precision_at_5":        None,
                "faithfulness":          None,
                "latency_ms":            latency_ms,
                "cache_creation_tokens": cache_cre,
                "cache_read_tokens":     cache_read,
                "input_tokens":          inp,
                "output_tokens":         out_tok,
                "cost_usd":              round(cost, 6),
                "reranker_cost_usd":     round(result.get("reranker_cost_usd", 0.0), 6),
                "reranked":              result.get("reranked", False),
            }

            out_f.write(json.dumps(record) + "\n")
            out_f.flush()
            results.append(record)

            print(f"{q['id']} [{q['category']}] -- done ({latency_ms}ms)")

    _print_summary(results)
    total_in_file = len(completed_ids) + len(results)
    print(f"\nResults written to {output} ({len(results)} new records, {total_in_file} total)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RAG eval harness")
    p.add_argument("--category", default=None,
                   help="Filter to one category (full name or c1-c5 alias)")
    p.add_argument("--limit", type=int, default=None,
                   help="Run only the first N questions")
    p.add_argument("--output", type=Path, default=DEFAULT_OUT_PATH,
                   help="Override output path (default: data/eval_results.jsonl)")
    p.add_argument("--fresh", action="store_true", default=False,
                   help="Force a clean run — truncate output file and run all questions")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    category = _resolve_category(args.category) if args.category else None
    run(category=category, limit=args.limit, output=args.output, fresh=args.fresh)


if __name__ == "__main__":
    main()
