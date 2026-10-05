#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker compose -f "$ROOT/docker-compose.conformance.yml" up -d --wait
python "$ROOT/scripts/wait_for_conformance_services.py"
printf 'PostgreSQL: postgresql://factgraph:factgraph@127.0.0.1:55432/factgraph\n'
printf 'MongoDB:    mongodb://127.0.0.1:57017\n'
printf 'TypeDB:     127.0.0.1:51729 (admin/password)\n'
