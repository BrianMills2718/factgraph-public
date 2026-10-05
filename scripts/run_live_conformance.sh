#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
PG_DSN="${FACTGRAPH_POSTGRES_DSN:-postgresql://factgraph:factgraph@127.0.0.1:55432/factgraph}"
MONGO_URI="${FACTGRAPH_MONGO_URI:-mongodb://127.0.0.1:57017}"
TYPEDB_ADDRESS="${FACTGRAPH_TYPEDB_ADDRESS:-127.0.0.1:51729}"
TYPEDB_USERNAME="${FACTGRAPH_TYPEDB_USERNAME:-admin}"
TYPEDB_PASSWORD="${FACTGRAPH_TYPEDB_PASSWORD:-password}"
OUT="$ROOT/artifacts/live_conformance"
mkdir -p "$OUT"

for src in "$ROOT"/examples/*.fg; do
  name="$(basename "$src" .fg)"
  rm -rf "$OUT/$name"
  python -m factgraph live-conformance "$src" \
    --out-dir "$OUT/$name" \
    --postgres-dsn "$PG_DSN" \
    --mongo-uri "$MONGO_URI" \
    --typedb-address "$TYPEDB_ADDRESS" \
    --typedb-username "$TYPEDB_USERNAME" \
    --typedb-password "$TYPEDB_PASSWORD"
done
