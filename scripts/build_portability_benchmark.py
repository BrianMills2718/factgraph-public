#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph import audit as semantic_audit  # noqa: E402
from factgraph.cli import load  # noqa: E402
from factgraph.conformance import bundle as conformance_bundle  # noqa: E402
from factgraph.live import mongo as live_mongo, postgres as live_postgres, typedb as live_typedb  # noqa: E402
from factgraph.mutations import write_mutation_catalog  # noqa: E402
from factgraph import shared_witness_execution as shared_witness  # noqa: E402
from factgraph.targets import mongo, postgres, typedb  # noqa: E402

BENCH = ROOT / "examples" / "portability"


def _matching(report: dict, assertion: dict) -> list[dict]:
    matches = [o for o in report["obligations"] if o["kind"] == assertion["kind"]]
    needle = assertion.get("reading_contains")
    if needle:
        matches = [o for o in matches if needle in o["reading"]]
    return matches


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _live_reports(model, args) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    reports["postgres"] = (
        live_postgres.run(model, args.postgres_dsn)
        if args.postgres_dsn else {"target": "postgres", "status": "not_run", "reason": "PostgreSQL DSN not supplied", "results": []}
    )
    reports["mongo"] = (
        live_mongo.run(model, args.mongo_uri)
        if args.mongo_uri else {"target": "mongo", "status": "not_run", "reason": "MongoDB URI not supplied", "results": []}
    )
    reports["typedb"] = (
        live_typedb.run(
            model, args.typedb_address,
            username=args.typedb_username, password=args.typedb_password, tls=args.typedb_tls,
        )
        if args.typedb_address else {"target": "typedb", "status": "not_run", "reason": "TypeDB address not supplied", "results": []}
    )
    return reports




def _shared_live_reports(model, args) -> dict[str, dict]:
    reports: dict[str, dict] = {}
    reports["postgres"] = (
        shared_witness.run_postgres(model, args.postgres_dsn)
        if args.postgres_dsn else {**shared_witness.generated_report(model, "postgres"), "status": "not_run", "reason": "PostgreSQL DSN not supplied"}
    )
    reports["mongo"] = (
        shared_witness.run_mongo(model, args.mongo_uri)
        if args.mongo_uri else {**shared_witness.generated_report(model, "mongo"), "status": "not_run", "reason": "MongoDB URI not supplied"}
    )
    reports["typedb"] = (
        shared_witness.run_typedb(
            model, args.typedb_address,
            username=args.typedb_username, password=args.typedb_password, tls=args.typedb_tls,
        )
        if args.typedb_address else {**shared_witness.generated_report(model, "typedb"), "status": "not_run", "reason": "TypeDB address not supplied"}
    )
    return reports

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the Factgraph semantic-portability benchmark evidence bundle")
    p.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "portability_benchmark")
    p.add_argument("--postgres-dsn", default=os.environ.get("FACTGRAPH_POSTGRES_DSN"))
    p.add_argument("--mongo-uri", default=os.environ.get("FACTGRAPH_MONGO_URI"))
    p.add_argument("--typedb-address", default=os.environ.get("FACTGRAPH_TYPEDB_ADDRESS"))
    p.add_argument("--typedb-username", default=os.environ.get("FACTGRAPH_TYPEDB_USERNAME", "admin"))
    p.add_argument("--typedb-password", default=os.environ.get("FACTGRAPH_TYPEDB_PASSWORD", "password"))
    p.add_argument("--typedb-tls", action="store_true")
    args = p.parse_args(argv)

    spec = json.loads((BENCH / "expectations.json").read_text(encoding="utf-8"))
    models = {Path(filename).stem: load(BENCH / filename) for filename in spec["cases"]}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    case_rows: list[dict] = []
    total_obligations = 0
    claim_falsifications = 0
    structural_failures = 0
    requested = {
        "postgres": bool(args.postgres_dsn),
        "mongo": bool(args.mongo_uri),
        "typedb": bool(args.typedb_address),
    }
    live_incomplete: list[dict] = []
    shared_live_incomplete: list[dict] = []
    shared_live_failures: list[dict] = []
    shared_live_totals = {
        "executable_case_count": 0,
        "poststate_required_count": 0,
        "poststate_query_ready_count": 0,
        "observed_asserted_case_count": 0,
        "poststate_pending_count": 0,
    }

    for filename, assertions in spec["cases"].items():
        stem = Path(filename).stem
        model = models[stem]
        out = args.out_dir / "cases" / stem
        live_reports = _live_reports(model, args)
        shared_live_reports = _shared_live_reports(model, args)
        report = semantic_audit.write_audit_bundle(
            model, out / "audit", targets=spec["targets"], live_reports=live_reports,
            shared_live_reports=shared_live_reports,
        )
        for target in spec["targets"]:
            shared = shared_live_reports[target]
            _write(out / "shared_witness_execution" / f"{target}.json", json.dumps(shared, indent=2, sort_keys=True) + "\n")
            shared_live_totals["executable_case_count"] += int(shared.get("executable_case_count", 0) or 0)
            shared_live_totals["poststate_required_count"] += int(shared.get("poststate_required_count", 0) or 0)
            shared_live_totals["poststate_query_ready_count"] += int(shared.get("poststate_query_ready_count", 0) or 0)
            shared_live_totals["observed_asserted_case_count"] += int(shared.get("asserted_case_count", 0) or 0)
            shared_live_totals["poststate_pending_count"] += int(shared.get("poststate_pending_count", 0) or 0)
        total_obligations += len(report["obligations"])
        claim_falsifications += sum(
            1 for ob in report["obligations"] for target in spec["targets"]
            if ob["verdicts"][target]["preservation"] == "claim_falsified"
        )
        conf = conformance_bundle(model)
        structural_failures += sum(1 for r in conf["static"]["results"] if not r["passed"])

        _write(out / "normalized.fg", __import__("factgraph.printer", fromlist=["print_model"]).print_model(model))
        _write(out / "postgres" / "model.sql", postgres.emit_sql(model))
        _write(out / "postgres" / "capabilities.json", postgres.capability_report(model).to_json())
        _write(out / "mongo" / "spec.json", mongo.emit_spec_json(model))
        _write(out / "mongo" / "capabilities.json", mongo.capability_report(model).to_json())
        _write(out / "typedb" / "schema.tql", typedb.emit_schema(model))
        _write(out / "typedb" / "capabilities.json", typedb.capability_report(model).to_json())
        conf_json = {
            "postgres_cases": [c.to_dict() for c in conf["postgres_cases"]],
            "mongo_cases": [c.to_dict() for c in conf["mongo_cases"]],
            "typedb_cases": [c.to_dict() for c in conf["typedb_cases"]],
            "static": conf["static"],
            "coverage": conf["coverage"],
        }
        _write(out / "conformance.json", json.dumps(conf_json, indent=2, sort_keys=True) + "\n")

        assertion_rows = []
        for assertion in assertions:
            matches = _matching(report, assertion)
            if len(matches) != 1:
                assertion_rows.append({"assertion": assertion, "passed": False, "reason": f"expected one matching obligation, found {len(matches)}"})
                continue
            obligation = matches[0]
            # The expectation file records structural/declarative verdicts. Live evidence may
            # strengthen `preserved_claimed` -> `preserved_observed` and `weakened` ->
            # `weakened_observed`; it may never silently change the semantic direction.
            actual_live = {t: obligation["verdicts"][t]["preservation"] for t in spec["targets"]}
            actual_base = {
                t: ("preserved_claimed" if v == "preserved_observed" else "weakened" if v == "weakened_observed" else v)
                for t, v in actual_live.items()
            }
            passed = actual_base == assertion["expected"]
            assertion_rows.append({
                "assertion": assertion,
                "obligation_id": obligation["id"],
                "reading": obligation["reading"],
                "actual": actual_live,
                "structural_direction": actual_base,
                "passed": passed,
            })

        for target, was_requested in requested.items():
            if was_requested and live_reports[target].get("status") != "completed":
                live_incomplete.append({"case": filename, "target": target, "status": live_reports[target].get("status"), "reason": live_reports[target].get("reason")})
            shared = shared_live_reports[target]
            if was_requested and shared.get("status") != "completed":
                shared_live_incomplete.append({"case": filename, "target": target, "status": shared.get("status"), "reason": shared.get("reason")})
            if was_requested and shared.get("status") == "completed" and shared.get("passed") is False:
                shared_live_failures.append({
                    "case": filename, "target": target,
                    "poststate_pending_count": shared.get("poststate_pending_count", 0),
                    "failed_obligations": [r.get("obligation_id") for r in shared.get("results", []) if r.get("passed") is False],
                })

        case_rows.append({
            "case": filename,
            "model": model.name,
            "obligation_count": len(report["obligations"]),
            "assertions": assertion_rows,
            "expectations_passed": all(r["passed"] for r in assertion_rows),
            "coverage_complete": conf["coverage"]["complete"],
            "structural_cases_pass": conf["static"]["all_passed"],
            "live_status": {t: live_reports[t].get("status") for t in spec["targets"]},
            "shared_witness_live_status": {t: shared_live_reports[t].get("status") for t in spec["targets"]},
            "shared_witness_live_passed": {t: shared_live_reports[t].get("passed") for t in spec["targets"]},
        })

    mutation_catalog = write_mutation_catalog(models, args.out_dir / "mutations")
    all_expectations = all(row["expectations_passed"] for row in case_rows)
    all_coverage = all(row["coverage_complete"] for row in case_rows)
    all_structural = all(row["structural_cases_pass"] for row in case_rows)
    requested_live_complete = not live_incomplete
    requested_shared_live_complete = not shared_live_incomplete
    shared_live_passed = not shared_live_failures
    summary = {
        "format": "factgraph-semantic-portability-benchmark-v1",
        "case_count": len(case_rows),
        "target_count": len(spec["targets"]),
        "targets": spec["targets"],
        "obligation_count": total_obligations,
        "mutation_count": mutation_catalog["mutation_count"],
        "expectations_passed": all_expectations,
        "coverage_complete": all_coverage,
        "structural_cases_pass": all_structural,
        "claim_falsification_count": claim_falsifications,
        "requested_live_targets": [k for k, v in requested.items() if v],
        "requested_live_complete": requested_live_complete,
        "live_incomplete": live_incomplete,
        "requested_shared_witness_live_complete": requested_shared_live_complete,
        "shared_witness_live_passed": shared_live_passed,
        "shared_witness_live_incomplete": shared_live_incomplete,
        "shared_witness_live_failures": shared_live_failures,
        "shared_witness_live_totals": shared_live_totals,
        "cases": case_rows,
        "contract": "The benchmark measures obligation-level semantic direction, conformance coverage, executable witness readiness, and—when target connections are supplied—observed target behavior. A missing live service is never counted as a pass.",
    }
    _write(args.out_dir / "BENCHMARK_SUMMARY.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")

    md = [
        "# Factgraph semantic portability benchmark",
        "",
        f"Cases: **{len(case_rows)}**  ",
        f"Targets: **{', '.join(spec['targets'])}**  ",
        f"Semantic obligations audited: **{total_obligations}**  ",
        f"Mutation experiments packaged: **{mutation_catalog['mutation_count']}**",
        "",
        "| Case | Expectations | Structural coverage | PG legacy live | Mongo legacy live | TypeDB legacy live | PG shared | Mongo shared | TypeDB shared |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in case_rows:
        md.append(
            f"| `{row['case']}` | {'PASS' if row['expectations_passed'] else 'FAIL'} | "
            f"{'PASS' if row['coverage_complete'] and row['structural_cases_pass'] else 'FAIL'} | "
            f"{row['live_status']['postgres']} | {row['live_status']['mongo']} | {row['live_status']['typedb']} | "
            f"{row['shared_witness_live_status']['postgres']} | {row['shared_witness_live_status']['mongo']} | {row['shared_witness_live_status']['typedb']} |"
        )
    md.extend([
        "",
        "> `not_run` is not evidence of preservation. Legacy live conformance and shared-source-witness execution are separate evidence channels.",
        "",
        "> Shared-witness acceptance is counted as semantic evidence for mandatory/subset/equality/symmetry only after the generated post-state query verifies that the missing counterpart is actually absent.",
        "",
        f"Shared-witness exact executable cases (generated when live is absent): **{shared_live_totals['executable_case_count']}**  ",
        f"Shared-witness post-state checks required / query-ready: **{shared_live_totals['poststate_required_count']} / {shared_live_totals['poststate_query_ready_count']}**",
        "",
    ])
    _write(args.out_dir / "BENCHMARK_MATRIX.md", "\n".join(md))

    ok = (
        all_expectations and all_coverage and all_structural and not claim_falsifications
        and requested_live_complete and requested_shared_live_complete and shared_live_passed
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
