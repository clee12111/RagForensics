"""
Corpus ingestion: clone FastAPI docs at a pinned release tag and write
structured markdown to data/corpus/ with a reproducibility manifest.

Ingestion only — no chunking, embedding, Pinecone, or LLM calls.
"""

import json
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

CACHE_DIR = PROJECT_ROOT / ".cache" / "fastapi-repo"
CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"

# Non-English language directory segments that must never appear in collected paths.
# If any collected file resolves to a path containing one of these segments the
# script raises an error instead of silently writing non-English content.
NON_ENGLISH_LANG_SEGMENTS = {
    "/de/", "/es/", "/fr/", "/ja/", "/ko/",
    "/pt/", "/ru/", "/tr/", "/zh/", "/zh-hant/", "/uk/",
}

# Files to exclude from ingestion even when found under the English root.
# - Underscore-prefixed files are internal repo tooling (e.g. _llm-test.md).
# - The named set lists known scaffolding files that are not user documentation.
EXCLUDED_FILENAMES = {
    "translation-banner.md",
    "missing-translation.md",
}
# Policy: also skip any file whose name starts with "_" (covers _llm-test.md
# and any future underscore-prefixed tooling files added to the repo).


# ── Git helpers ──────────────────────────────────────────────────────────────

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
    """Clone the FastAPI repo into CACHE_DIR, or fetch + checkout if it already exists."""
    if CACHE_DIR.exists():
        print(f"Cache hit: reusing {CACHE_DIR}")
        _run(["git", "fetch", "--tags", "--force"], cwd=CACHE_DIR)
    else:
        print(f"Cloning {FASTAPI_REPO_URL} into {CACHE_DIR} …")
        CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--filter=blob:none", FASTAPI_REPO_URL, str(CACHE_DIR)])

    # Verify the tag exists before checking out
    tags_raw = _run(["git", "tag", "--list", FASTAPI_VERSION], cwd=CACHE_DIR)
    if tags_raw != FASTAPI_VERSION:
        print(
            f"ERROR: tag '{FASTAPI_VERSION}' not found in the repository.\n"
            f"Available tags near that version (run: git tag --list '0.13*' in {CACHE_DIR})",
            file=sys.stderr,
        )
        sys.exit(1)

    _run(["git", "checkout", f"tags/{FASTAPI_VERSION}", "--detach"], cwd=CACHE_DIR)


def resolve_commit_hash() -> str:
    """Return the full commit SHA that HEAD (the checked-out tag) resolves to."""
    return _run(["git", "rev-parse", "HEAD"], cwd=CACHE_DIR)


# ── English-root resolution and guard ────────────────────────────────────────

def resolve_english_root() -> Path:
    """
    Resolve the English docs directory to an absolute path and structurally
    verify it ends with the expected .../en/docs segments.
    Raises SystemExit if the path does not exist or fails the segment check.
    """
    english_root = (CACHE_DIR / "docs" / "en" / "docs").resolve()

    if not english_root.exists():
        print(
            f"ERROR: English docs root not found: {english_root}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Structural assertion: the resolved path must end with en/docs
    parts = english_root.parts
    if len(parts) < 2 or parts[-2] != "en" or parts[-1] != "docs":
        print(
            f"ERROR: Resolved English root does not end with en/docs: {english_root}",
            file=sys.stderr,
        )
        sys.exit(1)

    return english_root


def assert_no_foreign_language(path: Path) -> None:
    """
    Belt-and-suspenders guard: verify a collected file path does not contain
    a non-English language directory segment. Raises SystemExit if it does.
    """
    # Normalise to forward slashes for consistent segment matching
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
    Copy qualifying .md files from english_root into CORPUS_DIR, preserving
    relative paths. Excludes scaffolding files (underscore-prefixed and named
    exclusions). Returns manifest metadata including excluded paths.
    """
    # Clean and recreate corpus dir for idempotency
    if CORPUS_DIR.exists():
        shutil.rmtree(CORPUS_DIR)
    CORPUS_DIR.mkdir(parents=True)

    all_md = sorted(english_root.rglob("*.md"))
    if not all_md:
        print(f"ERROR: no .md files found under {english_root}", file=sys.stderr)
        sys.exit(1)

    ingested_paths: list[str] = []
    excluded_paths: list[str] = []
    total_bytes = 0

    for src in all_md:
        # Belt-and-suspenders: reject any non-English path that somehow crept in
        assert_no_foreign_language(src.resolve())

        rel = src.relative_to(english_root)
        rel_posix = str(rel).replace("\\", "/")

        # Exclusion policy: skip underscore-prefixed files and named scaffolding
        if src.name.startswith("_") or src.name in EXCLUDED_FILENAMES:
            excluded_paths.append(rel_posix)
            continue

        dest = CORPUS_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_bytes()
        dest.write_bytes(content)
        total_bytes += len(content)
        ingested_paths.append(rel_posix)

    return {
        "ingested_paths": ingested_paths,
        "excluded_paths": excluded_paths,
        "total_bytes": total_bytes,
    }


# ── Manifest ──────────────────────────────────────────────────────────────────

def write_manifest(
    commit_hash: str,
    ingested_paths: list[str],
    excluded_paths: list[str],
    total_bytes: int,
) -> None:
    manifest = {
        "fastapi_version": FASTAPI_VERSION,
        "commit_hash": commit_hash,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "source_path": "docs/en/docs",
        "file_count": len(ingested_paths),
        "total_bytes": total_bytes,
        "excluded_files": excluded_paths,
        "files": ingested_paths,
    }
    manifest_path = CORPUS_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ensure_repo()
    commit_hash = resolve_commit_hash()
    english_root = resolve_english_root()
    result = ingest(english_root)

    ingested_paths = result["ingested_paths"]
    excluded_paths = result["excluded_paths"]
    total_bytes = result["total_bytes"]

    write_manifest(commit_hash, ingested_paths, excluded_paths, total_bytes)

    short_sha = commit_hash[:8]
    total_kb = total_bytes / 1024
    print(
        f"Ingested FastAPI {FASTAPI_VERSION} ({short_sha}) — "
        f"{len(ingested_paths)} files, {total_kb:.1f} KB "
        f"(excluded {len(excluded_paths)})"
    )


if __name__ == "__main__":
    main()
