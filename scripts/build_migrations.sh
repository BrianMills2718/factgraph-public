#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
BASE="$ROOT/examples/migrations"
OUT="$ROOT/artifacts/migrations"
rm -rf "$OUT"
mkdir -p "$OUT"
for dir in "$BASE"/*; do
  [ -d "$dir" ] || continue
  name="$(basename "$dir")"
  args=(python -m factgraph migrate "$dir/before.fg" "$dir/after.fg" --out-dir "$OUT/$name")
  if [ -f "$dir/hints.json" ]; then
    args+=(--hints "$dir/hints.json")
  fi
  "${args[@]}"
  mkdir -p "$OUT/$name/fixtures"
  for fixture in "$dir"/fixture.*.json; do
    [ -f "$fixture" ] || continue
    cp "$fixture" "$OUT/$name/fixtures/$(basename "$fixture")"
  done
done
