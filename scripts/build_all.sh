#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
mkdir -p "$ROOT/artifacts"

# Retained ordinary compiler fixtures.
for src in "$ROOT"/examples/*.fg; do
  name="$(basename "$src" .fg)"
  rm -rf "$ROOT/artifacts/$name"
  python -m factgraph build "$src" --out-dir "$ROOT/artifacts/$name"
done

# Retained infrastructure evidence.
"$ROOT/scripts/build_metamodel.sh" "$ROOT/artifacts/metamodel_self" >/dev/null
"$ROOT/scripts/build_migrations.sh"
"$ROOT/scripts/build_repository_demo.sh" >/dev/null
"$ROOT/scripts/build_collaboration_demo.sh" >/dev/null

# v0.12 semantic-portability evidence. These local builds are structural/static
# unless live DSNs are explicitly supplied to the dedicated live scripts.
rm -rf "$ROOT/artifacts/portability_benchmark" "$ROOT/artifacts/portability_mutations" "$ROOT/artifacts/external_audits" "$ROOT/artifacts/semantic_counterexample_benchmark" "$ROOT/artifacts/shared_witness_lowering_benchmark"
python "$ROOT/scripts/build_portability_benchmark.py" --out-dir "$ROOT/artifacts/portability_benchmark"
python "$ROOT/scripts/run_live_mutation_audit.py" --out-dir "$ROOT/artifacts/portability_mutations"
python "$ROOT/scripts/run_semantic_counterexample_benchmark.py" --out "$ROOT/artifacts/semantic_counterexample_benchmark/summary.json"
python "$ROOT/scripts/run_shared_witness_lowering_benchmark.py" --out "$ROOT/artifacts/shared_witness_lowering_benchmark/summary.json"
python -m factgraph audit "$ROOT/examples/external/ossie_people.yaml" --out-dir "$ROOT/artifacts/external_audits/ossie"
python -m factgraph audit "$ROOT/examples/external/linkml_people.yaml" --out-dir "$ROOT/artifacts/external_audits/linkml"

python -m factgraph audit "$ROOT/examples/external/factum_people.orm.json" --out-dir "$ROOT/artifacts/external_audits/factum"
python "$ROOT/scripts/build_v0_12_shared_witness_summary.py"
