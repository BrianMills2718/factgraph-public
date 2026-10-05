#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.audit import obligations
from factgraph.cli import load
from factgraph.witness import synthesize_counterexample


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit source-semantic counterexample synthesis across a Factgraph corpus.")
    ap.add_argument("--models", default="examples/portability", help="Directory containing .fg benchmark models")
    ap.add_argument("--out", default="artifacts/semantic_counterexample_benchmark/summary.json")
    args = ap.parse_args()

    root = Path(args.models)
    out = Path(args.out)
    cases = []
    status_counts: Counter[str] = Counter()
    kind_counts: dict[str, Counter[str]] = defaultdict(Counter)
    fact_rows: list[int] = []
    memberships: list[int] = []
    shrink_steps: list[int] = []
    local_count = 0

    for path in sorted(root.glob("*.fg")):
        model = load(path)
        case_rows = []
        for obligation in obligations(model):
            result = synthesize_counterexample(model, obligation.id, obligation.kind)
            status_counts[result.status] += 1
            kind_counts[obligation.kind][result.status] += 1
            local_count += int(result.locally_irreducible)
            row_count = sum(len(v) for v in result.population.facts.values())
            member_count = sum(len(v) for v in result.population.memberships.values())
            fact_rows.append(row_count)
            memberships.append(member_count)
            shrink_steps.append(result.shrink_steps)
            case_rows.append(
                {
                    "obligation_id": obligation.id,
                    "kind": obligation.kind,
                    "reading": obligation.reading,
                    "status": result.status,
                    "target_violation_observed": result.target_violation_observed,
                    "locally_irreducible": result.locally_irreducible,
                    "fact_row_count": row_count,
                    "membership_count": member_count,
                    "shrink_steps": result.shrink_steps,
                    "violation_codes": [v.code for v in result.violations],
                }
            )
        cases.append({"model": path.name, "obligation_count": len(case_rows), "obligations": case_rows})

    obligation_count = sum(status_counts.values())
    all_isolated = status_counts == Counter({"isolated": obligation_count})
    all_locally_irreducible = local_count == obligation_count
    report = {
        "format": "factgraph-semantic-counterexample-benchmark-v1",
        "model_count": len(cases),
        "obligation_count": obligation_count,
        "status_counts": dict(sorted(status_counts.items())),
        "all_isolated": all_isolated,
        "locally_irreducible_count": local_count,
        "all_locally_irreducible": all_locally_irreducible,
        "minimality_claim": "locally irreducible under deletion of one fact-row occurrence or typed population member; no global minimum claim",
        "population_size": {
            "fact_rows": {
                "min": min(fact_rows, default=0),
                "mean": round(mean(fact_rows), 4) if fact_rows else 0,
                "max": max(fact_rows, default=0),
            },
            "typed_memberships": {
                "min": min(memberships, default=0),
                "mean": round(mean(memberships), 4) if memberships else 0,
                "max": max(memberships, default=0),
            },
        },
        "shrink": {
            "total_deleted_atoms": sum(shrink_steps),
            "max_deleted_atoms_for_one_witness": max(shrink_steps, default=0),
        },
        "by_kind": {kind: dict(sorted(counts.items())) for kind, counts in sorted(kind_counts.items())},
        "cases": cases,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("model_count", "obligation_count", "status_counts", "all_isolated", "all_locally_irreducible")}, indent=2))
    return 0 if all_isolated and all_locally_irreducible else 1


if __name__ == "__main__":
    raise SystemExit(main())
