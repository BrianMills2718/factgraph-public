#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/../factgraph.zip}"
cd "$(dirname "$ROOT")"
rm -f "$OUT"
zip -qr "$OUT" "$(basename "$ROOT")" -x '*/__pycache__/*' -x '*/.pytest_cache/*'
printf '%s\n' "$OUT"
