#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.audit import obligations
from factgraph.cli import load
from factgraph.witness import synthesize_counterexample
from factgraph.witness_lowering import lower_population


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure exact shared-witness lowering across portability benchmark models.")
    ap.add_argument("--models", default="examples/portability")
    ap.add_argument("--out", default="artifacts/shared_witness_lowering_benchmark/summary.json")
    args = ap.parse_args()

    targets = ("postgres", "mongo", "typedb")
    counts = {t: Counter() for t in targets}
    by_kind = {t: defaultdict(Counter) for t in targets}
    cases = []
    all_targets_exact = 0
    total = 0

    for path in sorted(Path(args.models).glob("*.fg")):
        model = load(path)
        rows = []
        for ob in obligations(model):
            source = synthesize_counterexample(model, ob.id, ob.kind)
            target_rows = {}
            exact_all = True
            for target in targets:
                program = lower_population(model, source.population, target)
                counts[target][program.status] += 1
                by_kind[target][ob.kind][program.status] += 1
                exact_all = exact_all and program.status == "lowered"
                target_rows[target] = {
                    "status": program.status,
                    "fidelity": program.fidelity,
                    "operation_count": len(program.operations),
                    "limitations": list(program.limitations),
                }
            total += 1
            all_targets_exact += int(exact_all)
            rows.append({
                "obligation_id": ob.id,
                "kind": ob.kind,
                "source_counterexample_status": source.status,
                "all_targets_exact": exact_all,
                "targets": target_rows,
            })
        cases.append({"model": path.name, "obligation_count": len(rows), "obligations": rows})

    report = {
        "format": "factgraph-shared-witness-lowering-benchmark-v1",
        "model_count": len(cases),
        "obligation_count": total,
        "targets": {
            t: {
                "counts": dict(sorted(counts[t].items())),
                "exact_lowering_rate": round(counts[t]["lowered"] / total, 6) if total else 0,
                "by_kind": {k: dict(sorted(v.items())) for k, v in sorted(by_kind[t].items())},
            }
            for t in targets
        },
        "all_three_targets_exact_count": all_targets_exact,
        "all_three_targets_exact_rate": round(all_targets_exact / total, 6) if total else 0,
        "claim": "lowered means the emitted program is derived from the exact source semantic population; non-lowered cases are retained as explicit representation/context/identity gaps and are not replaced by handcrafted target witnesses",
        "cases": cases,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "model_count": report["model_count"],
        "obligation_count": total,
        "counts": {t: report["targets"][t]["counts"] for t in targets},
        "all_three_targets_exact_count": all_targets_exact,
    }, indent=2, sort_keys=True))
    # This benchmark is descriptive: gaps are expected and should remain visible.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
