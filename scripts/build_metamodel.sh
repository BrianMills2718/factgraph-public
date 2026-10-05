#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
OUT="${1:-$ROOT/artifacts/metamodel_self}"
rm -rf "$OUT"
python -m factgraph metamodel --out-dir "$OUT"
printf '%s\n' "$OUT"
