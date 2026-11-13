#!/usr/bin/env bash
# Refresh the mirror in this folder. Linux/macOS counterpart of run_mirror.bat.
set -euo pipefail
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
    exec uv run --script zotero_source.py --refresh "$@"
elif command -v python3 >/dev/null 2>&1; then
    echo "uv not found; falling back to python3 (needs requests and pymupdf installed)"
    exec python3 zotero_source.py --refresh "$@"
else
    echo "Neither uv nor python3 found on PATH." >&2
    exit 1
fi
