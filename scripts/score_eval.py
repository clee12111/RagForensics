"""
Interactive CLI for manual faithfulness and precision@5 scoring.

Usage:
    python scripts/score_eval.py                        # score all unscored
    python scripts/score_eval.py --category conceptual  # filter to one category

Faithfulness scale (printed once on startup):
    0 = hallucinated (contradicts or invents)
    1 = mostly wrong
    2 = partially correct
    3 = correct but incomplete
    4 = correct and complete
    5 = fully grounded, nothing added

Controls:
    s  — skip this question (leave unscored)
    q  — quit and print progress summary
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

RESULTS_PATH = Path("data/eval_results.jsonl")

DIVIDER = "-" * 57

FAITHFULNESS_SCALE = """\
Faithfulness scale:
  0 = hallucinated (contradicts or invents)
  1 = mostly wrong
  2 = partially correct
  3 = correct but incomplete
  4 = correct and complete
  5 = fully grounded, nothing added
"""

VALID_CATEGORIES = {
    "conceptual", "syntactic", "cross_reference", "edge_case", "out_of_scope",
}
_CATEGORY_ALIASES = {
    "c1": "conceptual", "c2": "syntactic", "c3": "cross_reference",
    "c4": "edge_case",  "c5": "out_of_scope",
}


# ── I/O helpers ───────────────────────────────────────────────────────────────

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


# ── Display ───────────────────────────────────────────────────────────────────

def _display_record(r: dict, position: int, total: int) -> None:
    print()
    print(DIVIDER)
    print(f"  [{r['id']}] {r['category']}  (Q {position} of {total})")
    print(DIVIDER)
    print("QUESTION:")
    print(r["question"])
    print()
    print("RETRIEVED CHUNKS:")
    for i, c in enumerate(r["chunks"], start=1):
        print(f"  [{i}] {c['source_file']}  (score={c['score']:.3f})")
    print()
    print("ANSWER:")
    print(r["answer"])
    print(DIVIDER)


# ── Input parsing ─────────────────────────────────────────────────────────────

def _parse_faithfulness(raw: str) -> int | None:
    """Return int 0-5, or None if invalid."""
    try:
        v = int(raw.strip())
        if 0 <= v <= 5:
            return v
    except ValueError:
        pass
    return None


def _parse_precision(raw: str) -> float | None:
    """
    Parse comma-separated chunk ranks (1-5) or '0' for none.
    Returns precision@5 as float (relevant / 5), or None if invalid.
    """
    raw = raw.strip()
    if raw == "0":
        return 0.0
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    try:
        ranks = [int(p) for p in parts]
    except ValueError:
        return None
    if not all(1 <= r <= 5 for r in ranks):
        return None
    relevant = len(set(ranks))
    return round(relevant / 5, 2)


# ── Summary ───────────────────────────────────────────────────────────────────

def _print_summary(records: list[dict]) -> None:
    cat_faith:  dict[str, list[float]] = defaultdict(list)
    cat_prec:   dict[str, list[float]] = defaultdict(list)

    for r in records:
        if r["faithfulness"] is not None:
            cat_faith[r["category"]].append(r["faithfulness"])
        if r["precision_at_5"] is not None:
            cat_prec[r["category"]].append(r["precision_at_5"])

    total_scored = sum(len(v) for v in cat_faith.values())
    if total_scored == 0:
        print("\nNo records scored yet.")
        return

    header = f"{'Category':<20} {'Scored':>6}  {'Mean Faith':>10}  {'Mean P@5':>8}"
    print("\n" + header)
    print("-" * len(header))

    for cat in sorted(cat_faith):
        faiths = cat_faith[cat]
        precs  = cat_prec.get(cat, [])
        mf = sum(faiths) / len(faiths)
        mp = sum(precs) / len(precs) if precs else 0.0
        print(f"{cat:<20} {len(faiths):>6}  {mf:>10.2f}  {mp:>8.2f}")

    print("-" * len(header))
    all_f = [v for vs in cat_faith.values() for v in vs]
    all_p = [v for vs in cat_prec.values() for v in vs]
    print(
        f"{'TOTAL':<20} {len(all_f):>6}  {sum(all_f)/len(all_f):>10.2f}"
        f"  {sum(all_p)/len(all_p) if all_p else 0.0:>8.2f}"
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(category: str | None = None) -> None:
    if not RESULTS_PATH.exists():
        print(f"Error: {RESULTS_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    records = _load_records()
    total   = len(records)

    # Build ordered list of indices to score (apply category filter if given)
    to_score = [
        i for i, r in enumerate(records)
        if r["faithfulness"] is None
        and (category is None or r["category"] == category)
    ]

    already_scored = total - len(
        [r for r in records if r["faithfulness"] is None]
    )

    if not to_score:
        print(f"All questions in scope already scored ({already_scored}/{total} total).")
        _print_summary(records)
        return

    first_id = records[to_score[0]]["id"]
    print(f"{already_scored} of {total} scored. Starting from question {first_id}.")
    print()
    print(FAITHFULNESS_SCALE)

    scored_this_session = 0

    for list_pos, rec_idx in enumerate(to_score, start=1):
        r        = records[rec_idx]
        position = already_scored + list_pos  # absolute question number in scope

        _display_record(r, position, total)

        # ── Faithfulness ─────────────────────────────────────────────────────
        while True:
            try:
                raw = input("Faithfulness (0-5, s=skip, q=quit): ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "q"

            if raw.lower() == "q":
                _print_summary(records)
                print(f"\nQuitting. {already_scored + scored_this_session}/{total} scored.")
                return
            if raw.lower() == "s":
                print("Skipped.")
                break

            faith = _parse_faithfulness(raw)
            if faith is None:
                print("  Enter a number 0-5, 's' to skip, or 'q' to quit.")
                continue

            # ── Precision@5 ──────────────────────────────────────────────────
            while True:
                try:
                    raw_p = input("Relevant chunks (e.g. 1,2,4 or 0 for none): ").strip()
                except (EOFError, KeyboardInterrupt):
                    raw_p = "q"

                if raw_p.lower() == "q":
                    # Save faithfulness before quitting
                    records[rec_idx]["faithfulness"] = faith
                    _save_records(records)
                    scored_this_session += 1
                    _print_summary(records)
                    print(f"\nQuitting. {already_scored + scored_this_session}/{total} scored.")
                    return

                prec = _parse_precision(raw_p)
                if prec is None:
                    print("  Enter comma-separated ranks 1-5 (e.g. 1,3,5) or 0 for none.")
                    continue

                # Write both scores
                records[rec_idx]["faithfulness"]  = faith
                records[rec_idx]["precision_at_5"] = prec
                _save_records(records)
                scored_this_session += 1
                remaining = len(to_score) - list_pos
                print(f"Saved. {remaining} remaining.")
                break

            break  # move to next question

    _print_summary(records)
    print(f"\nAll questions in scope scored. {already_scored + scored_this_session}/{total} total.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Interactive eval scorer")
    p.add_argument("--category", default=None,
                   help="Score one category only (full name or c1-c5 alias)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    category = None
    if args.category:
        raw = args.category.lower()
        category = _CATEGORY_ALIASES.get(raw, raw)
        if category not in VALID_CATEGORIES:
            print(f"Unknown category '{args.category}'.", file=sys.stderr)
            sys.exit(1)
    run(category=category)


if __name__ == "__main__":
    main()
