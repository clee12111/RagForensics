"""
Corpus ingestion: clone FastAPI docs at a pinned release tag, resolve all
{* ... *} include directives to inlined python fenced blocks, and write
structured markdown to data/corpus/ with a reproducibility manifest.

Ingestion only — no chunking, embedding, Pinecone, or LLM calls.
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

FASTAPI_VERSION = "0.136.1"
FASTAPI_REPO_URL = "https://github.com/fastapi/fastapi"

# Project root is two levels up from this script (scripts/ingest_corpus.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CACHE_DIR  = PROJECT_ROOT / ".cache" / "fastapi-repo"
CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"

# Anchor for directive path resolution.
# Directives use paths like ../../docs_src/... or ../../fastapi/... relative
# to docs/en/ (the language root), NOT to the individual markdown file's dir.
# Proven correct in Stage 2: 433/433 resolve.
REPO_LANG_ROOT = CACHE_DIR / "docs" / "en"

# Non-English language directory segments that must never appear in collected paths.
NON_ENGLISH_LANG_SEGMENTS = {
    "/de/", "/es/", "/fr/", "/ja/", "/ko/",
    "/pt/", "/ru/", "/tr/", "/zh/", "/zh-hant/", "/uk/",
}

# Files to exclude from ingestion even when found under the English root.
# - Underscore-prefixed files are internal repo tooling (e.g. _llm-test.md).
# - Named exclusions: scaffolding files and the release-notes changelog
#   (160K tokens of version history, not user documentation).
EXCLUDED_FILENAMES = {
    "translation-banner.md",
    "missing-translation.md",
    "release-notes.md",
}

# Pattern matching a full {* ... *} directive anywhere on a line.
_DIRECTIVE_RE = re.compile(r'\{\*\s*(.*?)\s*\*\}')


# ── Directive parser ──────────────────────────────────────────────────────────

def _parse_ranges(bracket_content: str) -> list[tuple[int, int]]:
    """
    Parse a comma-separated list of '1-indexed N' or 'N:M' range tokens.
    Single N becomes (N, N); N:M becomes (N, M). Whitespace after commas tolerated.
    """
    ranges = []
    for part in bracket_content.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            lo, hi = part.split(":", 1)
            ranges.append((int(lo.strip()), int(hi.strip())))
        else:
            n = int(part)
            ranges.append((n, n))
    return ranges


def _parse_directive(inner: str) -> dict:
    """
    Parse the text INSIDE a {* ... *} directive into structured fields:
      path  : str                        — .py file reference (relative to REPO_LANG_ROOT)
      ln    : list[(start,end)] | None   — line ranges to SELECT (1-indexed inclusive)
      hl    : list[(start,end)] | None   — emphasized lines (parsed but not emitted)
      title : str | None                 — display title string if present

    Raises ValueError with the offending inner text if parsing fails.
    All six corpus variants proven correct against 433 real directives (Stage 1).
    """
    inner = inner.strip()
    tokens = inner.split(None, 1)
    if not tokens:
        raise ValueError(f"Empty directive: {inner!r}")

    path = tokens[0]
    remainder = tokens[1] if len(tokens) > 1 else ""

    # title["..."] — extract and remove from remainder
    title = None
    title_m = re.search(r'title\["([^"]+)"\]', remainder)
    if title_m:
        title = title_m.group(1)
        remainder = remainder[:title_m.start()] + remainder[title_m.end():]

    # ln[...] — extract and remove from remainder
    ln = None
    ln_m = re.search(r'\bln\[\s*([\d:,\s]+?)\s*\]', remainder)
    if ln_m:
        ln = _parse_ranges(ln_m.group(1))
        remainder = remainder[:ln_m.start()] + remainder[ln_m.end():]

    # hl[...] — parse (for completeness) but do NOT emit (Decision B: hl is
    # presentation metadata with no plain-text equivalent; the faithful
    # representation of a highlighted block is just the code).
    hl = None
    hl_m = re.search(r'\bhl\[\s*([\d:,\s]+?)\s*\]', remainder)
    if hl_m:
        hl = _parse_ranges(hl_m.group(1))
        remainder = remainder[:hl_m.start()] + remainder[hl_m.end():]

    leftover = remainder.strip()
    if leftover:
        raise ValueError(
            f"Unparsed trailing content {leftover!r} in directive: {inner!r}"
        )

    return {"path": path, "ln": ln, "hl": hl, "title": title}


# ── Resolver and renderer ─────────────────────────────────────────────────────

def _resolve_path(ref_path: str) -> Path:
    """
    Resolve a directive ref_path to an absolute Path under REPO_LANG_ROOT.
    e.g. '../../docs_src/foo/bar.py' → CACHE_DIR/docs_src/foo/bar.py
    e.g. '../../fastapi/openapi/docs.py' → CACHE_DIR/fastapi/openapi/docs.py
    """
    return (REPO_LANG_ROOT / ref_path).resolve()


def _slice_lines(path: Path, ln_ranges: list[tuple[int, int]]) -> str:
    """
    Read path and return only the lines selected by ln_ranges.
    1-indexed inclusive on both ends. Multiple ranges concatenated in order.
    Proven no off-by-one against ln[9:24] ground truth (Stage 2).
    """
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    selected = []
    for start, end in ln_ranges:
        selected.extend(all_lines[start - 1 : end])
    return "".join(selected)


def _render_inlined(parsed: dict, resolved_path: Path) -> str:
    """
    Produce the replacement text for one {* ... *} directive.

    Rules (proven in Stage 3, hl-comment dropped per Decision B):
    - Content: ln[] present → slice to those lines; else whole file.
    - Wrap in a ```python fenced block.
    - title present → bold filename label on the line immediately before the fence.
    - No hl comment emitted (hl is presentation metadata; faithful repr is plain code).
    - No surrounding blank lines added — existing markdown blank lines provide spacing.
    """
    # Select content
    if parsed["ln"] is not None:
        content = _slice_lines(resolved_path, parsed["ln"])
    else:
        content = resolved_path.read_text(encoding="utf-8", errors="replace")

    parts: list[str] = []
    if parsed["title"]:
        parts.append(f"**`{parsed['title']}`**\n")
    parts.append("```python\n")
    parts.append(content if content.endswith("\n") else content + "\n")
    parts.append("```")

    return "".join(parts)


def _resolve_directives(content: str, src_file: Path) -> tuple[str, int]:
    """
    Find every {* ... *} in content, resolve it, and return the substituted
    string plus the count of directives resolved.

    FAILS LOUD: raises RuntimeError naming the offending directive and source
    file if any directive fails to parse or the referenced .py file is missing.
    A partial corpus is worse than a failed run.
    """
    count = 0
    errors: list[str] = []

    def replace_one(m: re.Match) -> str:
        nonlocal count
        inner = m.group(1)
        try:
            parsed = _parse_directive(inner)
        except ValueError as exc:
            errors.append(
                f"  Parse error in {src_file}: {exc}"
            )
            return m.group(0)  # leave raw so we can report, then fail below

        resolved = _resolve_path(parsed["path"])
        if not resolved.exists():
            errors.append(
                f"  Missing source in {src_file}: "
                f"directive {{* {inner} *}} resolved to {resolved} (not found)"
            )
            return m.group(0)

        count += 1
        return _render_inlined(parsed, resolved)

    result = _DIRECTIVE_RE.sub(replace_one, content)

    if errors:
        msg = "DIRECTIVE RESOLUTION FAILED — refusing to write partial corpus:\n"
        raise RuntimeError(msg + "\n".join(errors))

    return result, count


# ── Git helpers ───────────────────────────────────────────────────────────────

def _run(args: list[str], cwd: Path | None = None) -> str:
    """Run a subprocess, raise on non-zero exit, return stripped stdout."""
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: {' '.join(args)}\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def ensure_repo() -> None:
    """Clone the FastAPI repo into CACHE_DIR, or fetch + checkout if already present."""
    if CACHE_DIR.exists():
        print(f"Cache hit: reusing {CACHE_DIR}")
        _run(["git", "fetch", "--tags", "--force"], cwd=CACHE_DIR)
    else:
        print(f"Cloning {FASTAPI_REPO_URL} into {CACHE_DIR} …")
        CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--filter=blob:none", FASTAPI_REPO_URL, str(CACHE_DIR)])

    tags_raw = _run(["git", "tag", "--list", FASTAPI_VERSION], cwd=CACHE_DIR)
    if tags_raw != FASTAPI_VERSION:
        print(
            f"ERROR: tag '{FASTAPI_VERSION}' not found in the repository.",
            file=sys.stderr,
        )
        sys.exit(1)

    _run(["git", "checkout", f"tags/{FASTAPI_VERSION}", "--detach"], cwd=CACHE_DIR)


def resolve_commit_hash() -> str:
    return _run(["git", "rev-parse", "HEAD"], cwd=CACHE_DIR)


# ── English-root resolution and guard ────────────────────────────────────────

def resolve_english_root() -> Path:
    english_root = (CACHE_DIR / "docs" / "en" / "docs").resolve()
    if not english_root.exists():
        print(f"ERROR: English docs root not found: {english_root}", file=sys.stderr)
        sys.exit(1)
    parts = english_root.parts
    if len(parts) < 2 or parts[-2] != "en" or parts[-1] != "docs":
        print(
            f"ERROR: Resolved English root does not end with en/docs: {english_root}",
            file=sys.stderr,
        )
        sys.exit(1)
    return english_root


def assert_no_foreign_language(path: Path) -> None:
    posix = path.as_posix()
    for seg in NON_ENGLISH_LANG_SEGMENTS:
        if seg in posix:
            print(
                f"ERROR: non-English path leaked into collection: {path}\n"
                f"  Offending segment: {seg!r}",
                file=sys.stderr,
            )
            sys.exit(1)


# ── Ingestion ─────────────────────────────────────────────────────────────────

def ingest(english_root: Path) -> dict:
    """
    Collect qualifying .md files from english_root, resolve all {* ... *}
    directives to inlined python fenced blocks, and write to CORPUS_DIR.
    Returns manifest metadata.
    """
    if CORPUS_DIR.exists():
        shutil.rmtree(CORPUS_DIR)
    CORPUS_DIR.mkdir(parents=True)

    all_md = sorted(english_root.rglob("*.md"))
    if not all_md:
        print(f"ERROR: no .md files found under {english_root}", file=sys.stderr)
        sys.exit(1)

    ingested_paths:  list[str] = []
    excluded_paths:  list[str] = []
    total_bytes      = 0
    directives_resolved = 0

    for src in all_md:
        assert_no_foreign_language(src.resolve())

        rel       = src.relative_to(english_root)
        rel_posix = str(rel).replace("\\", "/")

        if src.name.startswith("_") or src.name in EXCLUDED_FILENAMES:
            excluded_paths.append(rel_posix)
            continue

        # Read as text, resolve directives, write resolved content.
        raw = src.read_text(encoding="utf-8", errors="replace")
        try:
            resolved_text, n_resolved = _resolve_directives(raw, src)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        directives_resolved += n_resolved

        encoded = resolved_text.encode("utf-8")
        dest = CORPUS_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(encoded)
        total_bytes += len(encoded)
        ingested_paths.append(rel_posix)

    return {
        "ingested_paths":     ingested_paths,
        "excluded_paths":     excluded_paths,
        "total_bytes":        total_bytes,
        "directives_resolved": directives_resolved,
    }


# ── Manifest ──────────────────────────────────────────────────────────────────

def write_manifest(
    commit_hash: str,
    ingested_paths: list[str],
    excluded_paths: list[str],
    total_bytes: int,
    directives_resolved: int,
) -> None:
    manifest = {
        "fastapi_version":    FASTAPI_VERSION,
        "commit_hash":        commit_hash,
        "ingested_at":        datetime.now(timezone.utc).isoformat(),
        "source_path":        "docs/en/docs",
        "file_count":         len(ingested_paths),
        "total_bytes":        total_bytes,
        "directives_resolved": directives_resolved,
        "excluded_files":     excluded_paths,
        "files":              ingested_paths,
    }
    (CORPUS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ensure_repo()
    commit_hash  = resolve_commit_hash()
    english_root = resolve_english_root()
    result       = ingest(english_root)

    write_manifest(
        commit_hash,
        result["ingested_paths"],
        result["excluded_paths"],
        result["total_bytes"],
        result["directives_resolved"],
    )

    short_sha  = commit_hash[:8]
    total_kb   = result["total_bytes"] / 1024
    n_files    = len(result["ingested_paths"])
    n_excluded = len(result["excluded_paths"])
    n_dir      = result["directives_resolved"]

    print(
        f"Ingested FastAPI {FASTAPI_VERSION} ({short_sha}) — "
        f"{n_files} files, {total_kb:.1f} KB, {n_dir} directives resolved "
        f"(excluded {n_excluded})"
    )


if __name__ == "__main__":
    main()
