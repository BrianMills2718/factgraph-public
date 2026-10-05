#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.cli import load  # noqa: E402
from factgraph.live import mongo as live_mongo, postgres as live_postgres, typedb as live_typedb  # noqa: E402
from factgraph.mutations import default_benchmark_mutations, evaluate_live_mutation, write_mutation_catalog  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run Factgraph mutation-oracle experiments against live targets")
    p.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "portability_mutations")
    p.add_argument("--postgres-dsn", default=os.environ.get("FACTGRAPH_POSTGRES_DSN"))
    p.add_argument("--mongo-uri", default=os.environ.get("FACTGRAPH_MONGO_URI"))
    p.add_argument("--typedb-address", default=os.environ.get("FACTGRAPH_TYPEDB_ADDRESS"))
    p.add_argument("--typedb-username", default=os.environ.get("FACTGRAPH_TYPEDB_USERNAME", "admin"))
    p.add_argument("--typedb-password", default=os.environ.get("FACTGRAPH_TYPEDB_PASSWORD", "password"))
    p.add_argument("--typedb-tls", action="store_true")
    args = p.parse_args()

    models = {path.stem: load(path) for path in sorted((ROOT / "examples" / "portability").glob("*.fg"))}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_mutation_catalog(models, args.out_dir / "catalog")
    mutations = default_benchmark_mutations(models)
    rows = []

    for mutation in mutations:
        model_key = next(k for k, m in models.items() if m.name == mutation.model)
        model = models[model_key]
        if mutation.target == "postgres":
            if not args.postgres_dsn:
                baseline = mutated = {"target": "postgres", "status": "not_run", "reason": "PostgreSQL DSN not supplied", "results": []}
            else:
                baseline = live_postgres.run(model, args.postgres_dsn)
                mutated = live_postgres.run(model, args.postgres_dsn, schema_sql_override=str(mutation.artifact))
        elif mutation.target == "mongo":
            if not args.mongo_uri:
                baseline = mutated = {"target": "mongo", "status": "not_run", "reason": "MongoDB URI not supplied", "results": []}
            else:
                baseline = live_mongo.run(model, args.mongo_uri)
                mutated = live_mongo.run(model, args.mongo_uri, spec_override=mutation.artifact)  # type: ignore[arg-type]
        elif mutation.target == "typedb":
            if not args.typedb_address:
                baseline = mutated = {"target": "typedb", "status": "not_run", "reason": "TypeDB address not supplied", "results": []}
            else:
                kwargs = {"username": args.typedb_username, "password": args.typedb_password, "tls": args.typedb_tls}
                baseline = live_typedb.run(model, args.typedb_address, **kwargs)
                mutated = live_typedb.run(model, args.typedb_address, schema_override=str(mutation.artifact), **kwargs)
        else:
            raise AssertionError(mutation.target)
        row = evaluate_live_mutation(mutation, baseline, mutated)
        rows.append(row)

    requested = {
        "postgres": bool(args.postgres_dsn),
        "mongo": bool(args.mongo_uri),
        "typedb": bool(args.typedb_address),
    }
    requested_rows = [r for r in rows if requested[r["mutation"]["target"]]]
    summary = {
        "format": "factgraph-live-mutation-suite-v1",
        "mutation_count": len(rows),
        "requested_targets": [k for k, v in requested.items() if v],
        "all_requested_mutations_detected": bool(requested_rows) and all(r["passed"] for r in requested_rows),
        "results": rows,
    }
    (args.out_dir / "live_mutation_results.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not requested_rows:
        return 0
    return 0 if summary["all_requested_mutations_detected"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
