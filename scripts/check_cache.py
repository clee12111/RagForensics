"""
Report prompt-cache token usage from an eval results file.

Usage:
    python scripts/check_cache.py                              # default: data/eval_results.jsonl
    python scripts/check_cache.py data/eval_results_fewshot.jsonl
"""

import json
import sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/eval_results.jsonl")
records = [json.loads(l) for l in path.open(encoding="utf-8")]

total_creation = sum(r.get("cache_creation_tokens", 0) for r in records)
total_read     = sum(r.get("cache_read_tokens", 0) for r in records)
hits           = sum(1 for r in records if r.get("cache_read_tokens", 0) > 0)

print(f"File: {path}  ({len(records)} records)")
print(f"cache_creation_tokens total : {total_creation:,}")
print(f"cache_read_tokens total     : {total_read:,}")
print(f"Questions with cache_read>0 : {hits} of {len(records)}")
print()
print("Per-record cache breakdown:")
print(f"  {'ID':<12} {'cache_creation':>16} {'cache_read':>12}")
print("  " + "-" * 42)
for r in records:
    cre = r.get("cache_creation_tokens", 0)
    rd  = r.get("cache_read_tokens", 0)
    print(f"  {r['id']:<12} {cre:>16,} {rd:>12,}")
