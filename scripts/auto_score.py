"""
Automated LLM-as-judge scoring for eval results.

Runs Gemini 2.5 Flash as primary judge, scoring faithfulness (0-5) and
precision@5 (chunk relevance) in a single call.

Unambiguous faithfulness scores (0, 1, 4, 5) → "gemini_clear"
Ambiguous scores (2, 3) → "flagged_ambiguous" (flagged for manual review)

Writes back to the results file in-place, flushing after each record.

Usage:
    python scripts/auto_score.py                        # score all unscored
    python scripts/auto_score.py --limit 5              # first N unscored only
    python scripts/auto_score.py --rescore              # re-judge ALL records
    python scripts/auto_score.py --rescore --limit 3    # re-judge first 3 only
    python scripts/auto_score.py --input data/eval_results_fewshot.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

_503_WAITS = [30, 60, 120]  # seconds; fail loud after 3 retries


def _score_with_retry(judge, question: str, chunks: list, answer: str):
    """Retry Gemini 503 UNAVAILABLE up to 3 times. All other errors propagate."""
    from google.genai.errors import ServerError  # type: ignore
    for attempt, wait in enumerate(_503_WAITS, start=1):
        try:
            return judge.score(question, chunks, answer)
        except ServerError as exc:
            if exc.code != 503:
                raise
            print(f"  503 unavailable -- retry {attempt}/3 in {wait}s")
            time.sleep(wait)
    return judge.score(question, chunks, answer)  # final attempt, fail loud

DEFAULT_RESULTS_PATH = Path("data/eval_results.jsonl")

_AMBIGUOUS = {2, 3}


# ── File I/O ──────────────────────────────────────────────────────────────────

def _load_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _save_records(records: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_summary(
    records: list[dict],
    total_cost: float,
    tally: dict[str, int],
) -> None:
    print(f"\nTotal judge cost: ${total_cost:.4f}")
    print(
        f"gemini_clear: {tally['gemini_clear']}  "
        f"flagged_ambiguous: {tally['flagged_ambiguous']}"
    )

    # out_of_scope is excluded from P@5 aggregates: by design, out_of_scope
    # questions have no relevant chunks (correct answer is refusal), so P@5=0
    # is the ideal score there — including it deflates the aggregate meaninglessly.
    _P5_EXCLUDE = {"out_of_scope"}

    cat_faith: dict[str, list[float]] = defaultdict(list)
    cat_p5:    dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.get("faithfulness") is not None:
            cat_faith[r["category"]].append(r["faithfulness"])
        if r.get("precision_at_5") is not None:
            cat_p5[r["category"]].append(r["precision_at_5"])

    if not cat_faith:
        return

    has_p5 = bool(cat_p5)
    if has_p5:
        header = f"{'Category':<20} {'Scored':>6}  {'Mean Faith':>10}  {'Mean P@5':>9}"
    else:
        header = f"{'Category':<20} {'Scored':>6}  {'Mean Faith':>10}"
    print("\n" + header)
    print("-" * len(header))
    for cat in sorted(cat_faith):
        vals  = cat_faith[cat]
        faith_mean = sum(vals) / len(vals)
        if has_p5 and cat_p5.get(cat):
            p5_mean = sum(cat_p5[cat]) / len(cat_p5[cat])
            note = " *" if cat in _P5_EXCLUDE else ""
            print(f"{cat:<20} {len(vals):>6}  {faith_mean:>10.2f}  {p5_mean:>9.2f}{note}")
        else:
            print(f"{cat:<20} {len(vals):>6}  {faith_mean:>10.2f}")
    all_vals = [v for vs in cat_faith.values() for v in vs]
    print("-" * len(header))
    if has_p5 and cat_p5:
        # P@5 aggregate excludes out_of_scope (inverse-relevance; see note above)
        p5_answerable = [
            v for cat, vs in cat_p5.items()
            if cat not in _P5_EXCLUDE
            for v in vs
        ]
        if p5_answerable:
            print(
                f"{'TOTAL':<20} {len(all_vals):>6}  {sum(all_vals)/len(all_vals):>10.2f}"
                f"  {sum(p5_answerable)/len(p5_answerable):>9.2f}"
                f"  (P@5 excl. out_of_scope)"
            )
        else:
            print(f"{'TOTAL':<20} {len(all_vals):>6}  {sum(all_vals)/len(all_vals):>10.2f}")
    else:
        print(f"{'TOTAL':<20} {len(all_vals):>6}  {sum(all_vals)/len(all_vals):>10.2f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    limit:   int | None = None,
    results: Path       = DEFAULT_RESULTS_PATH,
    rescore: bool       = False,
) -> None:
    from production_rag_forensics.eval.judge import Judge

    if not results.exists():
        print(f"Error: {results} not found.", file=sys.stderr)
        sys.exit(1)

    records = _load_records(results)

    if rescore:
        to_score = list(range(len(records)))
        print(f"Rescoring ALL {len(records)} records (--rescore).")
    else:
        to_score = [i for i, r in enumerate(records) if r.get("faithfulness") is None]

    if limit:
        to_score = to_score[:limit]

    if not to_score:
        print("All records already scored.")
        return

    print(f"Scoring {len(to_score)} records (of {len(records)} total).")

    gemini_judge = Judge(provider="gemini")

    total_cost = 0.0
    tally: dict[str, int] = {"gemini_clear": 0, "flagged_ambiguous": 0}

    for rec_idx in to_score:
        r = records[rec_idx]

        # ── Primary: Gemini ───────────────────────────────────────────────────
        g_result = _score_with_retry(gemini_judge, r["question"], r["chunks"], r["answer"])
        record_cost = g_result.cost_usd

        faith            = g_result.faithfulness
        reasoning        = g_result.reasoning
        relevant_chunks  = g_result.relevant_chunks
        precision_at_5   = g_result.precision_at_5
        relevance_reason = g_result.relevance_reason

        if faith not in _AMBIGUOUS:
            label = "gemini_clear"
            tally["gemini_clear"] += 1
        else:
            label = "flagged_ambiguous"
            tally["flagged_ambiguous"] += 1
            print(f"  [FLAG] {r['id']} scored {faith} — flagged for manual review")

        total_cost += record_cost

        # ── Write back ────────────────────────────────────────────────────────
        records[rec_idx]["faithfulness"]     = faith
        records[rec_idx]["judge_provider"]   = label
        records[rec_idx]["judge_reasoning"]  = reasoning
        records[rec_idx]["judge_cost_usd"]   = round(record_cost, 6)
        records[rec_idx]["precision_at_5"]   = round(precision_at_5, 2)
        records[rec_idx]["relevant_chunks"]  = relevant_chunks
        records[rec_idx]["relevance_reason"] = relevance_reason
        _save_records(records, results)

        print(
            f"{r['id']} [{r['category']}] -- "
            f"faith={faith} p@5={precision_at_5:.1f} "
            f"relevant={relevant_chunks} ({label}, ${record_cost:.4f})"
        )

    _print_summary(records, total_cost, tally)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM-as-judge auto-scorer")
    p.add_argument("--limit", type=int, default=None,
                   help="Score only first N records")
    p.add_argument("--results", "--input", dest="results", type=Path,
                   default=DEFAULT_RESULTS_PATH,
                   help="Path to eval results file (default: data/eval_results.jsonl)")
    p.add_argument("--rescore", action="store_true", default=False,
                   help="Re-judge ALL records, overwriting existing scores")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    run(limit=args.limit, results=args.results, rescore=args.rescore)


if __name__ == "__main__":
    main()
