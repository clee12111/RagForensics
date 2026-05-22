"""
Custom MCP server for Production RAG Stack Forensics.

Transport: Streamable HTTP (localhost:8001/mcp by default)
Auth:       Static bearer token (env MCP_BEARER_TOKEN). If unset, allows all
            requests (dev mode).

Six tools:
  journal_entries      — return recent journal entries from docs/journal.md
  failure_modes        — return failure mode entries from docs/failure_modes.md
  eval_results         — per-category faithfulness / P@5 summary from a named run
  retrieval_latency    — p50/p95 latency (ms) for the retrieve stage from Langfuse
  cost_per_query_stage — mean cost per stage (retrieve + generate) from Langfuse
  trace_query          — fetch a single trace by ID from Langfuse

Run:
    python -m production_rag_forensics.mcp_server.server
    # or:
    uvicorn production_rag_forensics.mcp_server.server:asgi_app --port 8001
"""
from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# ── Config ────────────────────────────────────────────────────────────────────

_BEARER_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")
_PORT = int(os.environ.get("MCP_PORT", "8001"))

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO = Path(__file__).parent.parent.parent.parent  # project root
_DOCS = _REPO / "docs"
_DATA = _REPO / "data"

# ── FastMCP instance ──────────────────────────────────────────────────────────

mcp = FastMCP(
    "rag-forensics",
    instructions=(
        "Forensic instrumentation for Production RAG Stack Forensics. "
        "Tools expose journal entries, failure modes, eval results, and "
        "Langfuse traces so an agent can reason over the live engineering record."
    ),
    port=_PORT,
    stateless_http=True,
)

# ── Auth middleware ───────────────────────────────────────────────────────────


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't carry the configured bearer token."""

    async def dispatch(self, request: Request, call_next):
        if not _BEARER_TOKEN:
            # No token configured → dev mode, allow all
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing Authorization header"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = auth.removeprefix("Bearer ").strip()
        if token != _BEARER_TOKEN:
            return JSONResponse(
                {"error": "Invalid bearer token"},
                status_code=403,
            )
        return await call_next(request)


# ── ASGI app (for uvicorn / Claude Desktop) ───────────────────────────────────

_base_app = mcp.streamable_http_app()
asgi_app = BearerTokenMiddleware(_base_app)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _read_doc(path: Path) -> str:
    if not path.exists():
        return f"[File not found: {path}]"
    return path.read_text(encoding="utf-8")


def _parse_journal_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    blocks = re.split(r"(?=^## \d{4}-\d{2}-\d{2})", text, flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^## (\d{4}-\d{2}-\d{2}) — (.+)$", block, re.MULTILINE)
        if not m:
            continue
        entries.append({"date": m.group(1), "title": m.group(2).strip(), "body": block})
    return entries


def _parse_failure_modes(text: str) -> list[dict[str, str]]:
    modes: list[dict[str, str]] = []
    blocks = re.split(r"(?=^## FM-)", text, flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^## (FM-\d+)[:\s]+(.+)$", block, re.MULTILINE)
        if not m:
            continue
        modes.append({"id": m.group(1), "title": m.group(2).strip(), "body": block})
    return modes


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines if l.strip()]


def _eval_summary(records: list[dict]) -> dict[str, Any]:
    from collections import defaultdict

    _P5_EXCLUDE = {"out_of_scope"}
    cat_faith: dict[str, list[float]] = defaultdict(list)
    cat_p5:    dict[str, list[float]] = defaultdict(list)

    for r in records:
        if r.get("faithfulness") is not None:
            cat_faith[r["category"]].append(r["faithfulness"])
        if r.get("precision_at_5") is not None and r["category"] not in _P5_EXCLUDE:
            cat_p5[r["category"]].append(r["precision_at_5"])

    rows = []
    for cat in sorted(cat_faith):
        vals = cat_faith[cat]
        row: dict[str, Any] = {
            "category": cat,
            "n": len(vals),
            "mean_faithfulness": round(sum(vals) / len(vals), 3),
        }
        if cat_p5.get(cat):
            p5 = cat_p5[cat]
            row["mean_precision_at_5"] = round(sum(p5) / len(p5), 3)
        rows.append(row)

    all_faith = [v for vs in cat_faith.values() for v in vs]
    all_p5    = [v for vs in cat_p5.values() for v in vs]
    total: dict[str, Any] = {
        "n": len(all_faith),
        "mean_faithfulness": round(sum(all_faith) / len(all_faith), 3) if all_faith else None,
    }
    if all_p5:
        total["mean_precision_at_5_excl_out_of_scope"] = round(sum(all_p5) / len(all_p5), 3)

    return {"categories": rows, "total": total}


def _get_langfuse():
    from langfuse import Langfuse
    pk   = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sk   = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")
    if not pk or not sk:
        raise RuntimeError("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
    return Langfuse(public_key=pk, secret_key=sk, host=host)


def _fetch_observations(stage: str, limit: int = 200) -> list[Any]:
    lf   = _get_langfuse()
    page = lf.api.observations.get_many(name=stage, limit=limit)
    return list(page.data) if page and page.data else []


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def journal_entries(last_n: int = 5) -> str:
    """
    Return the most recent N entries from docs/journal.md.

    Args:
        last_n: Number of most-recent entries to return (default 5, max 20).
    """
    last_n = min(max(1, last_n), 20)
    text    = _read_doc(_DOCS / "journal.md")
    entries = _parse_journal_entries(text)
    entries.sort(key=lambda e: e["date"], reverse=True)
    selected = entries[:last_n]
    if not selected:
        return "No journal entries found."
    out = [f"## {e['date']} — {e['title']}\n\n{e['body']}" for e in selected]
    return "\n\n---\n\n".join(out)


@mcp.tool()
def failure_modes(mode_id: str = "") -> str:
    """
    Return failure mode documentation from docs/failure_modes.md.

    Args:
        mode_id: Specific mode to return, e.g. 'FM-1'. Empty returns all modes.
    """
    text  = _read_doc(_DOCS / "failure_modes.md")
    modes = _parse_failure_modes(text)
    if mode_id:
        mode_id = mode_id.strip().upper()
        match = [m for m in modes if m["id"] == mode_id]
        if not match:
            available = [m["id"] for m in modes]
            return f"Mode '{mode_id}' not found. Available: {available}"
        return match[0]["body"]
    if not modes:
        return text
    return "\n\n---\n\n".join(m["body"] for m in modes)


@mcp.tool()
def eval_results(run: str = "optimized_run1") -> str:
    """
    Return per-category faithfulness and P@5 summary for a named eval run.

    Args:
        run: Run name suffix. Examples: 'optimized_run1', 'xprov_anthropic',
             'xprov_openai', 'xprov_google', 'fewshot'.
             Resolves to data/eval_results_{run}.jsonl.
    """
    path    = _DATA / f"eval_results_{run}.jsonl"
    records = _load_jsonl(path)
    if not records:
        available = sorted(
            p.stem.removeprefix("eval_results_")
            for p in _DATA.glob("eval_results_*.jsonl")
        )
        return json.dumps(
            {"error": f"No records for run '{run}'", "available_runs": available},
            indent=2,
        )

    scored  = [r for r in records if r.get("faithfulness") is not None]
    summary = _eval_summary(scored)
    summary["run"]            = run
    summary["total_records"]  = len(records)
    summary["scored_records"] = len(scored)

    models = {r.get("generation_model") for r in records if r.get("generation_model")}
    if models:
        summary["generation_models"] = sorted(models)

    return json.dumps(summary, indent=2)


@mcp.tool()
def retrieval_latency(limit: int = 200) -> str:
    """
    Return p50, p95, mean, min, max latency (ms) for the retrieve stage from Langfuse.

    Args:
        limit: Max number of recent retrieve spans to analyse (default 200).
    """
    try:
        obs = _fetch_observations("retrieve", limit=limit)
    except RuntimeError as e:
        return f"Error: {e}"

    # Langfuse returns latency in seconds; convert to ms
    latencies = sorted(
        o.latency * 1000 for o in obs if getattr(o, "latency", None) is not None
    )
    if not latencies:
        return json.dumps({"error": "No retrieve spans found in Langfuse"})

    n = len(latencies)
    return json.dumps({
        "stage":   "retrieve",
        "n":       n,
        "p50_ms":  round(latencies[int(n * 0.50)], 1),
        "p95_ms":  round(latencies[min(int(n * 0.95), n - 1)], 1),
        "mean_ms": round(statistics.mean(latencies), 1),
        "min_ms":  round(min(latencies), 1),
        "max_ms":  round(max(latencies), 1),
    }, indent=2)


@mcp.tool()
def cost_per_query_stage(run: str = "optimized_run1") -> str:
    """
    Return mean and total generation cost per query for a named eval run.
    Costs come from the eval JSONL (where per-query cost is faithfully recorded).

    Args:
        run: Run name suffix, e.g. 'optimized_run1', 'xprov_anthropic'.
             Resolves to data/eval_results_{run}.jsonl.
    """
    path    = _DATA / f"eval_results_{run}.jsonl"
    records = _load_jsonl(path)
    if not records:
        available = sorted(
            p.stem.removeprefix("eval_results_")
            for p in _DATA.glob("eval_results_*.jsonl")
        )
        return json.dumps(
            {"error": f"No records for run '{run}'", "available_runs": available},
            indent=2,
        )

    gen_costs    = [r["cost_usd"]        for r in records if r.get("cost_usd") is not None]
    judge_costs  = [r["judge_cost_usd"]  for r in records if r.get("judge_cost_usd") is not None]

    def _stats(costs: list[float], label: str) -> dict[str, Any]:
        if not costs:
            return {"n": 0, "mean_cost_usd": None, "total_cost_usd": None}
        return {
            "n":              len(costs),
            "mean_cost_usd":  round(statistics.mean(costs), 6),
            "total_cost_usd": round(sum(costs), 4),
        }

    result: dict[str, Any] = {
        "run":      run,
        "generate": _stats(gen_costs, "generate"),
        "judge":    _stats(judge_costs, "judge"),
    }

    # Include latency if present
    latencies = [r["latency_ms"] for r in records if r.get("latency_ms") is not None]
    if latencies:
        latencies.sort()
        n = len(latencies)
        result["latency"] = {
            "n":       n,
            "p50_ms":  round(latencies[int(n * 0.50)], 1),
            "p95_ms":  round(latencies[min(int(n * 0.95), n - 1)], 1),
            "mean_ms": round(statistics.mean(latencies), 1),
        }

    return json.dumps(result, indent=2)


@mcp.tool()
def trace_query(trace_id: str) -> str:
    """
    Fetch a single Langfuse trace by ID and return metadata, spans, and costs.

    Args:
        trace_id: Langfuse trace ID (visible in the Langfuse UI or eval JSONL records).
    """
    if not trace_id.strip():
        return "Error: trace_id is required."
    try:
        lf = _get_langfuse()
    except RuntimeError as e:
        return f"Error: {e}"

    try:
        trace = lf.api.trace.get(trace_id.strip())
    except Exception as e:
        return f"Error fetching trace '{trace_id}': {e}"

    try:
        obs_page = lf.api.observations.get_many(trace_id=trace_id.strip(), limit=50)
        obs_list = list(obs_page.data) if obs_page and obs_page.data else []
    except Exception:
        obs_list = []

    spans = [
        {
            "name":           getattr(o, "name", None),
            "type":           getattr(o, "type", None),
            "latency_ms":     getattr(o, "latency", None),
            "total_cost_usd": getattr(o, "total_cost", None),
            "usage_details":  getattr(o, "usage_details", None),
        }
        for o in obs_list
    ]

    return json.dumps(
        {
            "trace_id":   trace_id,
            "name":       getattr(trace, "name", None),
            "timestamp":  str(getattr(trace, "timestamp", "")),
            "total_cost": getattr(trace, "total_cost", None),
            "spans":      spans,
        },
        indent=2,
        default=str,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(asgi_app, host="127.0.0.1", port=_PORT)
