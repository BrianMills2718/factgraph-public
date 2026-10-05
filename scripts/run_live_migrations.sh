#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
PG_DSN="${FACTGRAPH_POSTGRES_DSN:-postgresql://factgraph:factgraph@127.0.0.1:55432/factgraph}"
MONGO_URI="${FACTGRAPH_MONGO_URI:-mongodb://127.0.0.1:57017}"
OUT="$ROOT/artifacts/live_migrations"
BASE="$ROOT/examples/migrations"
mkdir -p "$OUT"

# The safe_risky fixture family has both a passing and an intentionally failing
# before-population. The pass run should reach and verify the after schema when
# --allow-risky is enabled. The fail run should stop at a generated preflight.
for fixture_kind in pass fail; do
  dir="$BASE/safe_risky"
  out="$OUT/safe_risky/$fixture_kind"
  rm -rf "$out"
  set +e
  python -m factgraph live-migrate "$dir/before.fg" "$dir/after.fg" \
    --fixture "$dir/fixture.$fixture_kind.json" \
    --out-dir "$out" \
    --postgres-dsn "$PG_DSN" \
    --mongo-uri "$MONGO_URI" \
    --allow-risky
  rc=$?
  set -e
  printf '%s\n' "$rc" > "$out/EXIT_CODE.txt"
done

# The rename fixture is intentionally executed with safe-only policy. PostgreSQL
# can complete the hinted structural rename; MongoDB exposes the indexed-role
# rename as manual and therefore reports a partial run rather than guessing.
dir="$BASE/rename"
out="$OUT/rename/safe_only"
rm -rf "$out"
args=(python -m factgraph live-migrate "$dir/before.fg" "$dir/after.fg" --hints "$dir/hints.json" --out-dir "$out" --postgres-dsn "$PG_DSN" --mongo-uri "$MONGO_URI")
"${args[@]}"
