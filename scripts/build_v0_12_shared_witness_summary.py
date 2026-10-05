#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRICT = ROOT / "artifacts/shared_witness_lowering_benchmark/summary.json"
PORT = ROOT / "artifacts/portability_benchmark/BENCHMARK_SUMMARY.json"
CASES = ROOT / "artifacts/portability_benchmark/cases"
OUT_JSON = ROOT / "artifacts/V0_12_SHARED_WITNESS_SUMMARY.json"
OUT_MD = ROOT / "artifacts/V0_12_SHARED_WITNESS_MATRIX.md"
TARGETS = ("postgres", "mongo", "typedb")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    strict = _load(STRICT)
    port = _load(PORT)
    contextual = {}
    for target in TARGETS:
        statuses = Counter()
        fidelities = Counter()
        contextualized = 0
        post_required = 0
        post_ready = 0
        for path in sorted(CASES.glob(f"*/shared_witness_execution/{target}.json")):
            report = _load(path)
            rows = report.get("results") or report.get("cases") or []
            for row in rows:
                statuses[row.get("lowering_status", "unknown")] += 1
                fidelities[row.get("lowering_fidelity", "unknown")] += 1
                contextualized += int(bool(row.get("execution_context_added")))
                required = bool(row.get("poststate_required_for_semantic_proof"))
                post_required += int(required)
                post_ready += int(required and bool(row.get("postconditions")))
        contextual[target] = {
            "status_counts": dict(sorted(statuses.items())),
            "fidelity_counts": dict(sorted(fidelities.items())),
            "contextualized_case_count": contextualized,
            "poststate_required_count": post_required,
            "poststate_query_ready_count": post_ready,
        }

    summary = {
        "format": "factgraph-v0.12-shared-witness-summary-v1",
        "version": "0.12.0",
        "model_count": strict["model_count"],
        "obligation_count": strict["obligation_count"],
        "strict_minimal_source_witness": {
            "all_three_targets_exact_count": strict["all_three_targets_exact_count"],
            "targets": {t: strict["targets"][t]["counts"] for t in TARGETS},
            "claim": "Measures direct lowering of the unchanged locally irreducible source witness.",
        },
        "contextual_execution_witness": {
            "targets": contextual,
            "totals": port.get("shared_witness_live_totals", {}),
            "claim": "Context may be added once at the source-semantic level and is accepted only when the source oracle still reports exactly the intended obligation.",
        },
        "live_evidence": {
            "local_observed_asserted_case_count": port.get("shared_witness_live_totals", {}).get("observed_asserted_case_count", 0),
            "requested_shared_witness_live_complete": port.get("requested_shared_witness_live_complete"),
            "shared_witness_live_passed": port.get("shared_witness_live_passed"),
            "note": "No-service generation/readiness is not live target evidence.",
        },
        "nonclaims": [
            "No global minimum witness claim.",
            "No claim that every source-invalid state is physically realizable on every target.",
            "Transport support identities are not domain semantics.",
            "No local live database observation is claimed when services/drivers are unavailable.",
        ],
    }
    OUT_JSON.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# v0.12 shared semantic witness matrix",
        "",
        f"Corpus: **{summary['model_count']} models / {summary['obligation_count']} obligations**.",
        "",
        "## Strict locally irreducible source witness",
        "",
        "| Target | Lowered | Representation prevents exact realization | Unsupported |",
        "|---|---:|---:|---:|",
    ]
    for target in TARGETS:
        c = summary["strict_minimal_source_witness"]["targets"][target]
        lines.append(f"| {target} | {c.get('lowered', 0)} | {c.get('representation_prevents_exact_realization', 0)} | {c.get('unsupported', 0)} |")
    lines.extend([
        "",
        f"All three targets lower the unchanged minimal witness for **{strict['all_three_targets_exact_count']}** obligations.",
        "",
        "## Target-independent contextual execution witness",
        "",
        "| Target | Executable/lowered | Representation prevents exact realization | Unsupported | Contextualized | Post-state required/ready |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for target in TARGETS:
        row = contextual[target]
        c = row["status_counts"]
        lines.append(
            f"| {target} | {c.get('lowered', 0)} | {c.get('representation_prevents_exact_realization', 0)} | "
            f"{c.get('unsupported', 0)} | {row['contextualized_case_count']} | "
            f"{row['poststate_required_count']}/{row['poststate_query_ready_count']} |"
        )
    totals = summary["contextual_execution_witness"]["totals"]
    lines.extend([
        "",
        f"Total executable target cases: **{totals.get('executable_case_count', 0)}**.",
        f"Required post-state checks with generated queries: **{totals.get('poststate_query_ready_count', 0)}/{totals.get('poststate_required_count', 0)}**.",
        f"Locally live-observed asserted cases in this release environment: **{totals.get('observed_asserted_case_count', 0)}**.",
        "",
        "A representation-prevented case is retained as evidence about the target mapping. It is not replaced by a target-specific witness merely to increase the executable count.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON.relative_to(ROOT)),
        "markdown": str(OUT_MD.relative_to(ROOT)),
        "executable_case_count": totals.get("executable_case_count", 0),
        "poststate_ready": [totals.get("poststate_query_ready_count", 0), totals.get("poststate_required_count", 0)],
        "observed": totals.get("observed_asserted_case_count", 0),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
