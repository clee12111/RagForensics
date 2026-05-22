"""
Smoke test — run the same query through all three generation providers.

Usage:
    python scripts/smoke_providers.py

Prints answer (first 400 chars), token counts, cost, and latency per provider.
Does NOT run the full eval. Verify sane answers and correct model strings before
committing to 150-question runs.
"""

import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

QUERY = "How do I declare path parameters in FastAPI?"
PROVIDERS = ["anthropic", "openai", "google"]

for provider in PROVIDERS:
    print(f"\n{'=' * 60}")
    print(f"PROVIDER: {provider}")
    print("=" * 60)

    # Patch the provider flag before importing (or re-importing) the graph.
    # We reload the module each time so GENERATION_PROVIDER takes effect.
    import importlib
    import production_rag_forensics.orchestration.graph as graph_mod
    graph_mod.GENERATION_PROVIDER = provider

    # Also reload _graph so it's fresh (it's module-level compiled at import).
    # The graph itself doesn't hold provider state — the generate node reads
    # GENERATION_PROVIDER at call time, so no recompile needed.

    t0 = time.perf_counter()
    try:
        result = graph_mod.run_query(QUERY)
    except Exception as exc:
        print(f"ERROR: {exc}")
        continue
    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    print(f"Model              : {result.get('generation_model', 'n/a')}")
    print(f"Latency            : {elapsed_ms}ms")
    print(f"Input tokens       : {result.get('input_tokens', 0)}")
    print(f"Output tokens      : {result.get('output_tokens', 0)}")
    print(f"Cache creation tok : {result.get('cache_creation_tokens', 0)}")
    print(f"Cache read tokens  : {result.get('cache_read_tokens', 0)}")
    print(f"Cost (generation)  : ${result.get('cost_usd', 0.0):.6f}")
    answer = result.get("answer", "")
    print(f"\nAnswer ({len(answer)} chars):")
    print(answer[:600])
    if len(answer) > 600:
        print("... [truncated]")

print(f"\n{'=' * 60}")
print("Smoke test complete.")
