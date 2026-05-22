"""
Automated LLM-as-judge scoring for eval results.

Runs Gemini 2.0 Flash as primary judge. For ambiguous scores (2 or 3),
runs Sonnet 4.6 as calibration judge:
    - agree  → "calibrated"
    - disagree → "sonnet_override" (Sonnet wins)

Unambiguous scores (0, 1, 4, 5) → "gemini_clear"

Writes back to data/eval_results.jsonl in-place, flushing after each record.

Usage:
    python scripts/auto_score.py             # score all unscored
    python scripts/auto_score.py --limit 5   # first N unscored only
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

RESULTS_PATH = Path("data/eval_results.jsonl")

_AMBIGUOUS = {2, 3}


# ── File I/O ──────────────────────────────────────────────────────────────────

def _load_records() -> list[dict]:
    records = []
    with RESULTS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _save_records(records: list[dict]) -> None:
    with RESULTS_PATH.open("w", encoding="utf-8") as f:
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

    cat_faith: dict[str, list[float]] = defaultdict(list)
    for r in records:
        if r.get("faithfulness") is not None:
            cat_faith[r["category"]].append(r["faithfulness"])

    if not cat_faith:
        return

    header = f"{'Category':<20} {'Scored':>6}  {'Mean Faith':>10}"
    print("\n" + header)
    print("-" * len(header))
    for cat in sorted(cat_faith):
        vals = cat_faith[cat]
        print(f"{cat:<20} {len(vals):>6}  {sum(vals)/len(vals):>10.2f}")
    all_vals = [v for vs in cat_faith.values() for v in vs]
    print("-" * len(header))
    print(f"{'TOTAL':<20} {len(all_vals):>6}  {sum(all_vals)/len(all_vals):>10.2f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(limit: int | None = None) -> None:
    from production_rag_forensics.eval.judge import Judge

    if not RESULTS_PATH.exists():
        print(f"Error: {RESULTS_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    records  = _load_records()
    unscored = [i for i, r in enumerate(records) if r.get("faithfulness") is None]

    if limit:
        unscored = unscored[:limit]

    if not unscored:
        print("All records already scored.")
        return

    print(f"Scoring {len(unscored)} records (of {len(records)} total).")

    gemini_judge = Judge(provider="gemini")

    total_cost = 0.0
    tally: dict[str, int] = {"gemini_clear": 0, "flagged_ambiguous": 0}

    for rec_idx in unscored:
        r = records[rec_idx]

        # ── Primary: Gemini ───────────────────────────────────────────────────
        g_result = gemini_judge.score(r["question"], r["chunks"], r["answer"])
        record_cost = g_result.cost_usd

        faith     = g_result.faithfulness
        reasoning = g_result.reasoning

        if faith not in _AMBIGUOUS:
            label = "gemini_clear"
            tally["gemini_clear"] += 1
        else:
            # Ambiguous (2 or 3) — flag for manual review, do not call Sonnet
            label = "flagged_ambiguous"
            tally["flagged_ambiguous"] += 1
            print(f"  [FLAG] {r['id']} scored {faith} — flagged for manual review")

        total_cost += record_cost

        # ── Write back ────────────────────────────────────────────────────────
        records[rec_idx]["faithfulness"]    = faith
        records[rec_idx]["judge_provider"]  = label
        records[rec_idx]["judge_reasoning"] = reasoning
        records[rec_idx]["judge_cost_usd"]  = round(record_cost, 6)
        _save_records(records)

        print(
            f"{r['id']} [{r['category']}] -- "
            f"faith={faith} ({label}, ${record_cost:.4f})"
        )

    _print_summary(records, total_cost, tally)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM-as-judge auto-scorer")
    p.add_argument("--limit", type=int, default=None,
                   help="Score only first N unscored records")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    run(limit=args.limit)


if __name__ == "__main__":
    main()
