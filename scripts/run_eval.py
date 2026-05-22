"""
Thin wrapper around the eval harness.

Usage:
    python scripts/run_eval.py                         # full 150-question run
    python scripts/run_eval.py --category conceptual   # single category
    python scripts/run_eval.py --category c3           # alias for cross_reference
    python scripts/run_eval.py --limit 5               # first 5 questions (smoke test)
    python scripts/run_eval.py --output /tmp/out.jsonl # override output path
"""

import sys
from pathlib import Path

# Ensure src/ is on the path when run directly (editable install handles this too)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from production_rag_forensics.eval.harness import main

if __name__ == "__main__":
    main()
