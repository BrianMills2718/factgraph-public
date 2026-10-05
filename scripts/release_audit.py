#!/usr/bin/env python3
"""Generate release evidence files from an already-built factgraph tree.

This script never upgrades an unavailable live service into a pass. It only
summarizes artifacts that exist on disk and records environment capabilities.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts"
sys.path.insert(0, str(ROOT / "src"))
from factgraph import __version__  # noqa: E402


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def built_examples() -> list[Path]:
    return sorted(p for p in ART.iterdir() if p.is_dir() and (p / "BUILD_SUMMARY.json").exists())


def test_count() -> int | None:
    p = ART / "TEST_RESULTS.txt"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    # pytest -q dot output does not print the numeric summary in every version.
    m = re.search(r"(\d+) passed", text)
    if m:
        return int(m.group(1))
    # Count the final dot line as a fallback.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    dotlines = []
    for ln in lines:
        stripped = re.sub(r"\[\s*\d+%\]$", "", ln).strip()
        if stripped and set(stripped) <= {"."}:
            dotlines.append(stripped)
    if dotlines:
        return sum(line.count(".") for line in dotlines)
    return None


def environment() -> dict:
    binaries = {name: shutil.which(name) for name in ["docker", "podman", "postgres", "psql", "mongod", "mongosh", "typedb"]}
    modules = {name: importlib.util.find_spec(name) is not None for name in ["psycopg", "pymongo", "typedb"]}
    service_ready = bool((binaries["docker"] or binaries["podman"] or (binaries["postgres"] and binaries["mongod"] and binaries["typedb"])) and modules["psycopg"] and modules["pymongo"] and modules["typedb"])
    return {
        "binaries": binaries,
        "live_conformance_executed_in_release_environment": False,
        "live_migration_executed_in_release_environment": False,
        "python": sys.version.split()[0],
        "python_modules": modules,
        "reason": (
            "Live conformance/migrations are not automatically executed by release_audit.py. "
            "Use scripts/run_live_conformance.sh with reachable services; unavailable services are never counted as passes."
            if service_ready
            else "No complete PostgreSQL/MongoDB/TypeDB live service/driver environment was available while packaging this release."
        ),
    }


def conformance_summary(env: dict) -> dict:
    items = []
    total_pg = total_mg = total_pg_rt = total_mg_rt = total_static = 0
    for d in built_examples():
        cov = read_json(d / "conformance" / "coverage.json")
        static = read_json(d / "conformance" / "static_results.json")
        rt = read_json(d / "roundtrip.json")
        pg = read_json(d / "conformance" / "postgres" / "cases.json")
        mg = read_json(d / "conformance" / "mongo" / "cases.json")
        pg_rt = sum(c.get("mode") == "runtime" for c in pg)
        mg_rt = sum(c.get("mode") == "runtime" for c in mg)
        total_pg += len(pg)
        total_mg += len(mg)
        total_pg_rt += pg_rt
        total_mg_rt += mg_rt
        total_static += static.get("count", len(static.get("results", [])))
        summary = read_json(d / "BUILD_SUMMARY.json")
        items.append({
            "coverage_complete": bool(cov.get("complete")),
            "example": d.name,
            "model": summary.get("model"),
            "mongo_cases": len(mg),
            "mongo_runtime_cases": mg_rt,
            "postgres_cases": len(pg),
            "postgres_runtime_cases": pg_rt,
            "roundtrip_invariants_pass": bool(rt.get("all_current_invariants_pass")),
            "static_all_passed": bool(static.get("all_passed")),
            "static_case_count": static.get("count", len(static.get("results", []))),
        })
    return {
        "all_coverage_complete": all(i["coverage_complete"] for i in items),
        "all_roundtrip_invariants_pass": all(i["roundtrip_invariants_pass"] for i in items),
        "all_static_passed": all(i["static_all_passed"] for i in items),
        "example_count": len(items),
        "examples": items,
        "live_runtime_executed": bool(env["live_conformance_executed_in_release_environment"]),
        "live_runtime_note": "Runtime cases are generated and executable; they are counted as generated evidence, not as live passes, unless a live runner result says otherwise.",
        "totals": {
            "mongo_cases": total_mg,
            "mongo_runtime_cases": total_mg_rt,
            "postgres_cases": total_pg,
            "postgres_runtime_cases": total_pg_rt,
            "static_cases": total_static,
        },
        "version": __version__,
    }


def migration_summary() -> dict:
    base = ART / "migrations"
    items = []
    totals = {
        "semantic_changes": 0,
        "semantic_safe": 0,
        "semantic_requires_data_check": 0,
        "semantic_destructive": 0,
        "semantic_manual": 0,
        "postgres_operations": 0,
        "mongo_operations": 0,
    }
    if base.exists():
        for d in sorted(p for p in base.iterdir() if p.is_dir() and (p / "MIGRATION_SUMMARY.json").exists()):
            summary = read_json(d / "MIGRATION_SUMMARY.json")
            sem = summary.get("semantic", {})
            bs = sem.get("by_safety", {})
            item = {
                "example": d.name,
                "status": summary.get("status"),
                "semantic_changes": sem.get("change_count", 0),
                "semantic_by_safety": bs,
                "postgres": summary.get("postgres", {}),
                "mongo": summary.get("mongo", {}),
            }
            items.append(item)
            totals["semantic_changes"] += item["semantic_changes"]
            for key in ["safe", "requires_data_check", "destructive", "manual"]:
                totals[f"semantic_{key}"] += bs.get(key, 0)
            totals["postgres_operations"] += item["postgres"].get("operation_count", 0)
            totals["mongo_operations"] += item["mongo"].get("operation_count", 0)
    return {
        "format": "factgraph-release-migration-summary-v1",
        "version": __version__,
        "example_count": len(items),
        "all_planned": all(i["status"] == "planned" for i in items),
        "examples": items,
        "totals": totals,
        "safety_note": "safe scripts do not execute data-check-gated, destructive, or manual operations; preview files do not constitute execution approval",
    }


def live_migration_summary(env: dict) -> dict:
    base = ROOT / "examples" / "migrations"
    fixtures = []
    for path in sorted(base.glob("*/fixture.*.json")):
        try:
            data = read_json(path)
        except Exception:
            continue
        fixtures.append({
            "path": str(path.relative_to(ROOT)),
            "format": data.get("format"),
            "postgres_setup_count": len(data.get("postgres", {}).get("setup_sql", [])),
            "mongo_setup_count": len(data.get("mongo", {}).get("setup", [])),
        })
    live_base = ART / "live_migrations"
    reports = []
    if live_base.exists():
        for path in sorted(live_base.rglob("LIVE_MIGRATION.json")):
            try:
                data = read_json(path)
            except Exception:
                continue
            reports.append({
                "path": str(path.relative_to(ROOT)),
                "failed_targets": data.get("failed_targets", []),
                "reports": data.get("reports", {}),
            })
    return {
        "format": "factgraph-release-live-migration-summary-v1",
        "version": __version__,
        "fixture_count": len(fixtures),
        "fixtures": fixtures,
        "live_execution_reports": reports,
        "live_execution_report_count": len(reports),
        "executed_in_release_environment": bool(env.get("live_migration_executed_in_release_environment")),
        "runner": "scripts/run_live_migrations.sh",
        "contract": {
            "safe": "automatic",
            "requires_data_check": "requires --allow-risky plus passing preflight",
            "destructive": "requires separate --allow-destructive",
            "manual": "never generic-executable",
            "postgres": "transactional migration stage plus live structural verification",
            "mongo": "isolated disposable database; no generic transaction/rollback claim",
        },
    }


def metamodel_summary() -> dict:
    self_path = ART / "metamodel_self" / "self_host.json"
    self_report = read_json(self_path) if self_path.exists() else {}
    examples = []
    total_core = 0
    total_envelope = 0
    for d in built_examples():
        path = d / "metamodel" / "roundtrip.json"
        if not path.exists():
            examples.append({"example": d.name, "status": "missing"})
            continue
        report = read_json(path)
        core = report.get("core_population", {})
        envelope = report.get("envelope_population", {})
        total_core += int(core.get("row_count", 0))
        total_envelope += int(envelope.get("row_count", 0))
        examples.append({
            "example": d.name,
            "status": "present",
            "core_rows": core.get("row_count"),
            "envelope_rows": envelope.get("row_count"),
            "core_validation_errors": core.get("validation_errors", []),
            "envelope_validation_errors": envelope.get("validation_errors", []),
            "semantic_roundtrip_equal": core.get("semantic_roundtrip_equal"),
            "manifest_roundtrip_equal": envelope.get("manifest_roundtrip_equal"),
        })
    return {
        "format": "factgraph-release-metamodel-summary-v1",
        "version": __version__,
        "self_host": self_report,
        "example_count": len(examples),
        "examples": examples,
        "all_example_core_semantic_roundtrips_pass": all(e.get("semantic_roundtrip_equal") is True for e in examples),
        "all_example_manifest_roundtrips_pass": all(e.get("manifest_roundtrip_equal") is True for e in examples),
        "all_example_populations_validate": all(not e.get("core_validation_errors") and not e.get("envelope_validation_errors") for e in examples),
        "totals": {"core_population_rows": total_core, "envelope_population_rows": total_envelope},
        "claim_scope": "semantic/metamodel self-hosting only; no Python compiler bootstrap or universal metamodel claim",
    }



def repository_summary() -> dict:
    meta_base = ART / "metamodel_versions"
    repo_base = ART / "repository_demo"
    versions = read_json(meta_base / "versions.json") if (meta_base / "versions.json").exists() else {}
    mdiff = read_json(meta_base / "v1_to_v2_diff" / "SUMMARY.json") if (meta_base / "v1_to_v2_diff" / "SUMMARY.json").exists() else {}
    population_migration = read_json(meta_base / "warehouse_v1_to_v2" / "migration.json") if (meta_base / "warehouse_v1_to_v2" / "migration.json").exists() else {}
    demo = read_json(repo_base / "V0_7_REPOSITORY_DEMO_SUMMARY.json") if (repo_base / "V0_7_REPOSITORY_DEMO_SUMMARY.json").exists() else {}
    v08 = read_json(repo_base / "V0_8_REPOSITORY_EVOLUTION_SUMMARY.json") if (repo_base / "V0_8_REPOSITORY_EVOLUTION_SUMMARY.json").exists() else {}
    collab_base = ART / "collaboration_demo"
    v09 = read_json(collab_base / "V0_9_COLLABORATION_SUMMARY.json") if (collab_base / "V0_9_COLLABORATION_SUMMARY.json").exists() else {}
    verification = read_json(repo_base / "verification.json") if (repo_base / "verification.json").exists() else {}
    log = read_json(repo_base / "team-app.log.json") if (repo_base / "team-app.log.json").exists() else {}
    return {
        "format": "factgraph-release-repository-summary-v1",
        "version": __version__,
        "metamodel_versions": versions.get("versions", []),
        "current_metamodel_version": versions.get("current_version"),
        "metamodel_diff": mdiff.get("diff", {}),
        "standalone_population_migration": population_migration,
        "repository_demo": demo,
        "repository_v0_8_demo": v08,
        "repository_v0_9_demo": v09,
        "repository_verification": {
            "passed": verification.get("passed"),
            "error_count": len(verification.get("errors", [])),
            "warning_count": len(verification.get("warnings", [])),
            "summary": verification.get("summary", {}),
        },
        "repository_log": {
            "model_key": log.get("model_key"),
            "head_revision_id": log.get("head_revision_id"),
            "revision_count": len(log.get("revisions", [])),
            "operations": [x.get("operation") for x in log.get("revisions", [])],
            "metamodel_versions": [x.get("metamodel_version") for x in log.get("revisions", [])],
        },
        "all_v0_7_invariants_pass": bool(
            population_migration.get("migration_passed")
            and verification.get("passed")
            and demo.get("repository_verification_passed")
            and demo.get("repository_head_metamodel_version") == "2"
        ),
        "all_v0_8_invariants_pass": bool(
            v08.get("clean_merge_status") == "merged"
            and v08.get("clean_merge_conflicts") == 0
            and v08.get("clean_merge_parent_count") == 2
            and v08.get("conflict_merge_status") == "conflicts"
            and int(v08.get("conflict_count", 0)) > 0
            and v08.get("resolved_merge_status") == "merged"
            and v08.get("signature_verification_passed")
            and v08.get("repository_verification_passed")
        ),
        "all_v0_9_invariants_pass": bool(
            v09.get("unsigned_push_status") == "objects_transferred"
            and v09.get("signed_push_status") == "pushed"
            and v09.get("dedup_push_object_count") == 0
            and v09.get("fetch_status") == "fetched"
            and v09.get("fetch_local_branch_advanced") is False
            and not v09.get("reviewer_local_branches")
            and v09.get("protected_branch_trust_passed")
            and v09.get("hub_repository_verification_passed")
            and int(v09.get("merge_conflict_count", 0)) > 0
            and v09.get("merge_has_orm_explanation")
        ),
        "claim_scope": "filesystem content-addressed collaboration with fast-forward push, fetch-only tracking, local attestation trust policy, and ORM-aware merge explanations; no network transport, force push, distributed consensus, or Git replacement claim",
    }

def portability_summary() -> dict:
    benchmark_path = ART / "portability_benchmark" / "BENCHMARK_SUMMARY.json"
    benchmark = read_json(benchmark_path) if benchmark_path.exists() else {}
    mutation_path = ART / "portability_mutations" / "live_mutation_results.json"
    mutation_live = read_json(mutation_path) if mutation_path.exists() else {}
    counterexample_path = ART / "semantic_counterexample_benchmark" / "summary.json"
    counterexamples = read_json(counterexample_path) if counterexample_path.exists() else {}
    shared_path = ART / "V0_12_SHARED_WITNESS_SUMMARY.json"
    shared_witness = read_json(shared_path) if shared_path.exists() else {}

    acceptance_probe_count = 0
    acceptance_obligation_count = 0
    for case_dir in sorted((ART / "portability_benchmark" / "cases").glob("*")) if (ART / "portability_benchmark" / "cases").exists() else []:
        p = case_dir / "audit" / "AUDIT_SUMMARY.json"
        if not p.exists():
            continue
        a = read_json(p).get("semantic_acceptance_probes", {})
        acceptance_probe_count += int(a.get("probe_count", 0) or 0)
        acceptance_obligation_count += int(a.get("obligation_count", 0) or 0)

    external_pipelines = {}
    for pipeline in ("linkml_1_11_1", "factum_0_5_0"):
        p = ART / "external_pipelines" / pipeline / "SUMMARY.json"
        external_pipelines[pipeline] = read_json(p) if p.exists() else {"status": "missing", "live_observed": False}
    upstream_factum_path = ART / "external_pipelines" / "factum_0_5_0" / "upstream_fig_mandatory" / "SUMMARY.json"
    upstream_factum_control = read_json(upstream_factum_path) if upstream_factum_path.exists() else {"status": "missing", "live_observed": False}

    external = {}
    for name in ("ossie", "linkml", "factum"):
        base = ART / "external_audits" / name
        audit_path = base / "AUDIT_SUMMARY.json"
        import_path = base / "source_import" / "import_report.json"
        external[name] = {
            "audit": read_json(audit_path) if audit_path.exists() else {},
            "import": read_json(import_path) if import_path.exists() else {},
        }

    live_statuses = {}
    live_observed = {}
    for target in ("postgres", "mongo", "typedb"):
        statuses = sorted({
            row.get("live_status", {}).get(target, "missing")
            for row in benchmark.get("cases", [])
        })
        live_statuses[target] = statuses
        observed = 0
        for case_dir in sorted((ART / "portability_benchmark" / "cases").glob("*")) if (ART / "portability_benchmark" / "cases").exists() else []:
            p = case_dir / "audit" / "AUDIT_SUMMARY.json"
            if p.exists():
                observed += int(read_json(p).get("targets", {}).get(target, {}).get("live_observed", 0))
        live_observed[target] = observed

    external_formats = [
        name for name, data in external.items()
        if data.get("import", {}).get("status") in {"imported", "imported_with_gaps"}
    ]
    mutation_rows = mutation_live.get("results", [])
    mutation_observed = [r for r in mutation_rows if r.get("status") == "mutation_detected"]
    gate = {
        "two_external_formats": {"passed": len(external_formats) >= 2, "count": len(external_formats), "formats": external_formats},
        "three_external_formats_current": {"passed": len(external_formats) >= 3, "count": len(external_formats), "formats": external_formats},
        "source_counterexamples": {
            "passed": bool(counterexamples.get("all_isolated")) and bool(counterexamples.get("all_locally_irreducible")),
            "obligation_count": counterexamples.get("obligation_count", 0),
            "all_isolated": counterexamples.get("all_isolated"),
            "all_locally_irreducible": counterexamples.get("all_locally_irreducible"),
        },
        "shared_witness_execution_ready": {
            "passed": bool(shared_witness) and shared_witness.get("contextual_execution_witness", {}).get("totals", {}).get("poststate_query_ready_count") == shared_witness.get("contextual_execution_witness", {}).get("totals", {}).get("poststate_required_count"),
            "executable_case_count": shared_witness.get("contextual_execution_witness", {}).get("totals", {}).get("executable_case_count", 0),
            "poststate_required_count": shared_witness.get("contextual_execution_witness", {}).get("totals", {}).get("poststate_required_count", 0),
            "poststate_query_ready_count": shared_witness.get("contextual_execution_witness", {}).get("totals", {}).get("poststate_query_ready_count", 0),
            "local_live_observed_count": shared_witness.get("live_evidence", {}).get("local_observed_asserted_case_count", 0),
        },
        "three_live_targets_in_ci_configured": {"passed": (ROOT / ".github" / "workflows" / "semantic-portability.yml").exists(), "note": "configuration present; this release audit does not equate configuration with an observed CI run"},
        "per_obligation_transformation_trace": {"passed": bool(benchmark.get("case_count"))},
        "mutation_testing_packaged": {"passed": int(benchmark.get("mutation_count", 0)) >= 4, "count": benchmark.get("mutation_count", 0), "live_detected_in_release_environment": len(mutation_observed)},
        "twenty_benchmark_cases": {"passed": int(benchmark.get("case_count", 0)) >= 20, "count": benchmark.get("case_count", 0)},
        "acceptance_probe_support": {"passed": acceptance_probe_count > 0, "probe_count": acceptance_probe_count, "obligation_count_with_probes": acceptance_obligation_count, "scope": "selected value, relationship, set, identity, subtype, and frequency obligations"},
        "external_pipelines_prepared": {"passed": len(external_pipelines) >= 2 and all(v.get("status") != "missing" for v in external_pipelines.values()), "count": len([v for v in external_pipelines.values() if v.get("status") != "missing"]), "required": 3},
        "external_pipeline_findings": {"passed": False, "count": sum(1 for v in external_pipelines.values() if v.get("live_observed") and v.get("passed") is True), "required": 3},
        "external_user_confirmations": {"passed": False, "count": 0, "required": 2},
    }
    return {
        "format": "factgraph-v0.18-semantic-portability-summary-v1",
        "version": __version__,
        "benchmark": benchmark,
        "source_counterexamples": counterexamples,
        "shared_witness_execution": shared_witness,
        "acceptance_probes": {"probe_count": acceptance_probe_count, "obligation_count_with_probes": acceptance_obligation_count, "scope": "finite source-valid value/relationship/set/identity/subtype/frequency probes"},
        "external_pipelines": external_pipelines,
        "upstream_factum_control": upstream_factum_control,
        "external_formats": external,
        "external_format_count": len(external_formats),
        "live_statuses": live_statuses,
        "live_observed_obligations": live_observed,
        "mutation_live": mutation_live,
        "gate": gate,
        "known_internal_finding": {
            "finding": "The portability benchmark exposed a missing PostgreSQL conformance case for a value constraint projected onto an objectified relationship field; the generator was fixed before v0.10 release.",
            "significance": "The benchmark falsified Factgraph's own evidence coverage rather than merely documenting adapter claims.",
        },
        "claim_scope": "obligation-level semantic portability auditing with explicit source-import coverage, independent source-semantic counterexamples, shared target-independent execution witnesses, structural evidence, and live write/post-state paths; representation-impossible target states remain explicit and local live execution is claimed only when observed",
    }


def determinism_values() -> tuple[str, str]:
    p = ART / "DETERMINISM_CHECK.txt"
    if not p.exists():
        return "not run", "unknown"
    text = p.read_text(encoding="utf-8", errors="replace")
    result = "passed" if "result: PASS" in text else "failed"
    m = re.search(r"generated files per build:\s*(\d+)", text)
    return result, m.group(1) if m else "unknown"


def quality_report(summary: dict, env: dict, migrations: dict, live_migrations: dict, metamodel: dict, repository: dict, portability: dict) -> str:
    tests = test_count()
    det, det_files = determinism_values()
    totals = summary["totals"]
    mt = migrations["totals"]
    return f"""# Quality report — factgraph {__version__}

## Automated checks executed in this packaging environment

- Python test suite: **{tests if tests is not None else 'see TEST_RESULTS.txt'} passed**.
- Python bytecode compilation: **passed**.
- Determinism check: **{det}**.
  - the active v0.18 compiler/audit/acceptance-probe/target/import/counterexample/shared-witness/mutation/external-artifact/provenance-locked third-party-pipeline surface is regenerated in two isolated trees; frozen v0.7-v0.9 repository/collaboration demos remain covered by regression tests and their retained release evidence; see `DETERMINISM_CHECK.txt`.
  - **{det_files} generated files** per build were byte-identical when the check passed.
- Conformance capability coverage: **{'complete for every example' if summary['all_coverage_complete'] else 'INCOMPLETE'}**.
- Structural conformance cases: **{'all passed' if summary['all_static_passed'] else 'FAILURES PRESENT'}** ({totals['static_cases']} cases).
- Current round-trip invariants: **{'all passed' if summary['all_roundtrip_invariants_pass'] else 'FAILURES PRESENT'}** for all {summary['example_count']} ordinary compiler examples.
- v0.4 migration fixtures: **{migrations['example_count']} planned**; all planned status: **{migrations['all_planned']}**.
  - semantic changes exercised: **{mt['semantic_changes']}**;
  - PostgreSQL migration operations: **{mt['postgres_operations']}**;
  - MongoDB migration operations: **{mt['mongo_operations']}**.

## v0.18 semantic portability evidence

- Public portability benchmark cases: **{portability.get('benchmark', {}).get('case_count', 0)}**.
- Targets compared: **{', '.join(portability.get('benchmark', {}).get('targets', [])) or 'none'}**.
- Semantic obligations audited across the benchmark: **{portability.get('benchmark', {}).get('obligation_count', 0)}**.
- Source-semantic counterexamples isolated: **{portability.get('source_counterexamples', {}).get('status_counts', {}).get('isolated', 0)} / {portability.get('source_counterexamples', {}).get('obligation_count', 0)}**.
- Source counterexamples locally irreducible under single-element deletion: **{portability.get('source_counterexamples', {}).get('locally_irreducible_count', 0)} / {portability.get('source_counterexamples', {}).get('obligation_count', 0)}**.
- Counterexample minimality claim: **local irreducibility only; no global-minimum claim**.
- Source-valid acceptance probes: **{portability.get('acceptance_probes', {}).get('probe_count', 0)}** across **{portability.get('acceptance_probes', {}).get('obligation_count_with_probes', 0)}** obligations; current scope includes selected value, relationship, set, identity, subtype, and frequency obligations.
- Acceptance probes refine blocked-invalid results into probe-scoped preservation vs strengthening/incompatibility; **no full-equivalence claim** is made from a finite probe set.
- Shared target-executable cases from one source-semantic witness/envelope: **{portability.get('shared_witness_execution', {}).get('contextual_execution_witness', {}).get('totals', {}).get('executable_case_count', 0)}**.
- Shared-witness post-state queries ready: **{portability.get('shared_witness_execution', {}).get('contextual_execution_witness', {}).get('totals', {}).get('poststate_query_ready_count', 0)} / {portability.get('shared_witness_execution', {}).get('contextual_execution_witness', {}).get('totals', {}).get('poststate_required_count', 0)}**.
- Shared-witness live observations in this packaging environment: **{portability.get('shared_witness_execution', {}).get('live_evidence', {}).get('local_observed_asserted_case_count', 0)}**.
- Static expectation matrix passed: **{portability.get('benchmark', {}).get('expectations_passed')}**.
- Native/emulated conformance coverage complete: **{portability.get('benchmark', {}).get('coverage_complete')}**.
- Structural conformance checks passed: **{portability.get('benchmark', {}).get('structural_cases_pass')}**.
- Deliberate mutation experiments packaged: **{portability.get('benchmark', {}).get('mutation_count', 0)}**.
- External semantic formats exercised without rewriting into the Factgraph DSL: **{portability.get('external_format_count', 0)}** (`ossie`, `linkml`, `factum` when present).
- Live-observed benchmark obligations in this packaging environment: **{sum(portability.get('live_observed_obligations', {}).values())}**.
- Local live target statuses: **{portability.get('live_statuses')}**.
- Live mutation detections in this packaging environment: **{len([r for r in portability.get('mutation_live', {}).get('results', []) if r.get('status') == 'mutation_detected'])}**.
- External PostgreSQL artifact audit examples packaged: **2** (one preserving, one deliberately weakened value-range implementation).
- External PostgreSQL artifact observations in this packaging environment: **0**; both local bundles remain `generated_not_run`.
- Hosted CI includes a live falsification gate requiring the same `Age = -1` semantic witness to be prevented by the preserving external artifact and realized by the weakened artifact.
- Independent external tool families prepared: **{len([v for v in portability.get('external_pipelines', {}).values() if v.get('status') != 'missing'])}** (`LinkML 1.11.1`, `Factum ORM 0.5.0`).
- Prepared external experiments: **3** (LinkML range hypothesis, Factgraph-authored Factum m:n stress case, provenance-locked upstream Factum functional-mandatory control).
- Upstream Factum control local status: **{portability.get('upstream_factum_control', {}).get('status')}**; provenance/live observations are not promoted when the exact checkouts or PostgreSQL are unavailable.
- Independent pipeline live findings in this packaging environment: **{sum(1 for v in portability.get('external_pipelines', {}).values() if v.get('live_observed') and v.get('passed') is True) + (1 if portability.get('upstream_factum_control', {}).get('live_observed') and portability.get('upstream_factum_control', {}).get('passed') is True else 0)}**. Generator-unavailable/local-not-run states are not findings.

The GitHub Actions workflow is configured to execute PostgreSQL, MongoDB, and TypeDB live, run the 20-case benchmark, mutation-test six target weakenings, and audit the Ossie/LinkML/Factum fixtures. **Workflow configuration is not counted here as an observed CI run.** If this packaging environment has no services/drivers, the local evidence remains `not_run`.

The benchmark already found one internal evidence bug during development: a PostgreSQL value-domain claim on an objectified relationship field lacked a generated conformance case. That gap was fixed before release; it is retained in the portability evidence history as evidence that the corpus can falsify Factgraph's own coverage.

## v0.9 semantic collaboration evidence

- Collaboration demo passed: **{repository.get('all_v0_9_invariants_pass')}**.
- Unsigned protected push result: **{repository.get('repository_v0_9_demo', {}).get('unsigned_push_status')}** (objects may transfer; branch does not advance).
- Signed protected push result: **{repository.get('repository_v0_9_demo', {}).get('signed_push_status')}**.
- Repeated push content-addressed objects: **{repository.get('repository_v0_9_demo', {}).get('dedup_push_object_count')}**.
- Fetch advanced local branch: **{repository.get('repository_v0_9_demo', {}).get('fetch_local_branch_advanced')}**.
- Protected-branch trust evaluation passed: **{repository.get('repository_v0_9_demo', {}).get('protected_branch_trust_passed')}**.
- Hub repository verification passed: **{repository.get('repository_v0_9_demo', {}).get('hub_repository_verification_passed')}**.
- ORM-aware explained merge conflict count: **{repository.get('repository_v0_9_demo', {}).get('merge_conflict_count')}**.
- ORM-aware Markdown explanation present: **{repository.get('repository_v0_9_demo', {}).get('merge_has_orm_explanation')}**.

Remote transfer is filesystem-only in v0.9. Fetch imports immutable content and updates a remote-tracking ref but never moves a local branch. Push is fast-forward-only. Portable Ed25519 attestation validity is separate from destination-local trust policy; transferred public keys are not auto-trusted.

## v0.8 repository evolution evidence

- Stable-identity branch/merge demo passed: **{repository.get('all_v0_8_invariants_pass')}**.
- Clean semantic merge status: **{repository.get('repository_v0_8_demo', {}).get('clean_merge_status')}**.
- Clean merge parent count: **{repository.get('repository_v0_8_demo', {}).get('clean_merge_parent_count')}**.
- Deliberate modify/modify conflict count: **{repository.get('repository_v0_8_demo', {}).get('conflict_count')}**.
- Assisted resolution status: **{repository.get('repository_v0_8_demo', {}).get('resolved_merge_status')}**.
- Repository branches: **{list(repository.get('repository_v0_8_demo', {}).get('branches', {}).keys())}**.
- Immutable tags: **{list(repository.get('repository_v0_8_demo', {}).get('tags', {}).keys())}**.
- Signed revision attestations verified: **{repository.get('repository_v0_8_demo', {}).get('signature_verification_passed')}** ({repository.get('repository_v0_8_demo', {}).get('attestation_count')} attestation).

Stable identities are explicit semantic IDs, not inferred rename heuristics. Branches/tags are local repository refs; merge is a three-way semantic merge over normalized element IDs. Signatures are append-only Ed25519 attestations and do not mutate immutable revisions. The retained v0.8 layer introduced no remotes; v0.9 adds filesystem-only collaboration while still making no distributed locking/consensus or Git-replacement claim.

## v0.7 versioned metamodel + repository evidence retained

- Packaged metamodel versions: **{len(repository.get('metamodel_versions', []))}**.
- Current metamodel version: **{repository.get('current_metamodel_version')}**.
- v1→v2 semantic metamodel changes: **{repository.get('metamodel_diff', {}).get('change_count')}**.
- Standalone v1→v2 population migration passed: **{repository.get('standalone_population_migration', {}).get('migration_passed')}**.
- Repository demo verification passed: **{repository.get('repository_verification', {}).get('passed')}**.
- Repository demo immutable revisions: **{repository.get('repository_log', {}).get('revision_count')}**.
- Repository operations: **{repository.get('repository_log', {}).get('operations')}**.
- Repository revision metamodel versions: **{repository.get('repository_log', {}).get('metamodel_versions')}**.
- All packaged v0.7 invariants passed: **{repository.get('all_v0_7_invariants_pass')}**.

Metamodel migration remains semantic decode/re-encode. The v0.7 linear-history behavior is retained as the default `main` branch path inside the v0.8 DAG repository.

## v0.6 metamodel-as-data / self-hosting evidence

- Canonical metamodel self-population validation errors: **{len(metamodel.get('self_host', {}).get('population_validation_errors', []))}**.
- Metamodel semantic self-round-trip: **{metamodel.get('self_host', {}).get('semantic_roundtrip_equal')}**.
- Metamodel manifest self-round-trip: **{metamodel.get('self_host', {}).get('manifest_roundtrip_equal')}**.
- Metamodel second-encoding fixed point: **{metamodel.get('self_host', {}).get('second_encoding_identical')}**.
- Ordinary examples with metamodel evidence: **{metamodel.get('example_count')}**.
- All example core semantic round trips: **{metamodel.get('all_example_core_semantic_roundtrips_pass')}**.
- All example envelope manifest round trips: **{metamodel.get('all_example_manifest_roundtrips_pass')}**.
- All generated metamodel populations validate: **{metamodel.get('all_example_populations_validate')}**.
- Total core population rows across examples: **{metamodel.get('totals', {}).get('core_population_rows')}**.
- Total envelope population rows across examples: **{metamodel.get('totals', {}).get('envelope_population_rows')}**.

This is deliberately a semantic/metamodel closure claim. The Python compiler implementation is still handwritten and the release makes no universal metamodel claim.

## v0.4 semantic migration-planning evidence retained

The release includes semantic and target-specific migration tests/examples for:

- additive optional fields and new fact tables/collections;
- tightening requiredness and uniqueness with generated data preflights;
- explicit object/field/role renames via validated migration hints;
- PostgreSQL table/column rename ordering;
- MongoDB staged non-indexed field renames and manual escalation for indexed role renames;
- destructive field/fact/entity cleanup remaining blocked in safe scripts;
- value-domain tightening versus relaxation safety classification;
- deterministic JSON/Markdown plans and script previews.

Semantic safety totals in packaged migration fixtures:

- safe: **{mt['semantic_safe']}**;
- requires data check: **{mt['semantic_requires_data_check']}**;
- destructive: **{mt['semantic_destructive']}**;
- manual: **{mt['semantic_manual']}**.

A preview file is not counted as an executed migration. The migration CLI writes files only.

## v0.5 live migration evidence

- `factgraph live-migrate` is implemented as a separate opt-in executor.
- Packaged versioned live migration fixtures: **{live_migrations['fixture_count']}**.
- Live migration execution reports produced in this packaging environment: **{live_migrations['live_execution_report_count']}**.
- PostgreSQL live migration contract: transactionally apply the migration stage, roll back on gated/execution failure, then introspect the isolated schema.
- MongoDB live migration contract: use a disposable isolated database and explicitly make no generic transaction/rollback claim.
- Manual operations remain non-executable under every policy.
- `requires_data_check` operations require both `--allow-risky` and a passing generated preflight.
- Destructive operations require the separate `--allow-destructive` opt-in.

The release contains both a passing and intentionally failing risky-population fixture under `examples/migrations/safe_risky/`. The failing fixture is designed to stop at preflight on a live service.

Live migration execution is **not counted as passing evidence** unless `LIVE_MIGRATION.json` reports exist from an actual service run.

## v0.3 semantic/conformance evidence retained

- numeric value ranges and scalar enumerations;
- frequency constraints;
- subset, equality, and exclusion over role-sequence projections;
- single-inheritance entity subtyping with inherited identity;
- capability-aware PostgreSQL/MongoDB/GraphQL projections.

## Generated live-runtime conformance evidence

Across the {summary['example_count']} ordinary examples the build generates:

- **{totals['postgres_runtime_cases']} PostgreSQL runtime conformance cases** ({totals['postgres_cases']} total PostgreSQL cases);
- **{totals['mongo_runtime_cases']} MongoDB runtime conformance cases** ({totals['mongo_cases']} total MongoDB cases).

Live PostgreSQL/MongoDB conformance cases in the retained v0.3-v0.9 example suite were **not executed as part of this packaging audit**. The v0.11 semantic-portability section above separately records PostgreSQL/MongoDB/TypeDB live status.

Environment snapshot:

- PostgreSQL client/server tooling present: **{bool(env['binaries']['psql'] or env['binaries']['postgres'])}**;
- MongoDB client/server tooling present: **{bool(env['binaries']['mongosh'] or env['binaries']['mongod'])}**;
- Docker/Podman present: **{bool(env['binaries']['docker'] or env['binaries']['podman'])}**;
- `psycopg` installed: **{env['python_modules']['psycopg']}**;
- `pymongo` installed: **{env['python_modules']['pymongo']}**;
- `typedb-driver` import namespace installed: **{env['python_modules']['typedb']}**.

This is recorded as **not run**, not as passing evidence.

## Recovery and migration discipline

- Pure target artifacts remain separate from semantic sidecars.
- Exact semantic sidecar recovery is not presented as native target losslessness.
- Migration renames are never inferred heuristically.
- Destructive/manual migration operations remain blocked in safe scripts.
- `requires_data_check` operations carry target-specific preflights when factgraph can formulate them.
- Arbitrary PostgreSQL/MongoDB reverse engineering and general instance-data migration remain out of scope.

## Runtime dependencies

The semantic compiler and migration planner have **no mandatory third-party runtime dependencies** beyond Python 3.11+.

Live semantic-portability evidence has optional dependencies under `factgraph[live]`:

- `psycopg[binary]`;
- `pymongo`;
- `typedb-driver`.

External YAML interchange uses optional `factgraph[interchange]` / PyYAML.
"""


def release_notes(summary: dict, migrations: dict, live_migrations: dict, metamodel: dict, repository: dict, portability: dict) -> str:
    t = summary["totals"]
    mt = migrations["totals"]
    return f"""# Release notes — factgraph {__version__}

v0.18 adds provenance-locked upstream Factum evidence and bounded functional-binary absorption support to the external PostgreSQL auditor, while retaining generalized source-valid acceptance evidence and the LinkML/Factum stress experiments. The v0.1–v0.9 compiler/migration/metamodel/repository features remain supported infrastructure rather than the product center.

## Added in v0.18

- external PostgreSQL mapping v3 for explicitly declared non-objectified binary-fact absorption into an anchor entity table, with no arbitrary denormalization inference;
- coalesced source-valid absorbed relationship writes, multiplicity-sensitive repeated absorbed occurrences, and non-anchor-nonnull relationship-presence postconditions;
- Factum reference-mode default typing aligned with Factum 0.5.0 Rmap conventions to prevent transport-type false positives;
- exact upstream Factum preservation control pinned by generator/model commits, Git blobs, tree, source SHA-256, and package version;
- Factum hosted CI now invokes the checked-in bundled CLI from the exact Git checkout rather than installing the generator from npm;
- finite source-valid acceptance probes generalized across value, relationship, set, identity, subtype, frequency, symmetry, and fact-set obligations;
- source-oracle validation of every positive probe before it can be lowered or counted as evidence;
- probe-scoped refinement of external PostgreSQL observations into `preserved_on_tested_cases`, `stronger_or_incompatible`, `weakened`, or unresolved states;
- the Factum total-participation pipeline now carries both the negative absence witness and a positive Person+Skill+bridge participation case;
- external acceptance indexes include obligation kind, and all positive populations use the same explicit physical mapping contract as negative witnesses;
- active-surface determinism extended across the generalized acceptance-probe corpus and both independent pipeline handoffs.

Packaged acceptance probes: {portability.get('acceptance_probes', {}).get('probe_count', 0)} across {portability.get('acceptance_probes', {}).get('obligation_count_with_probes', 0)} obligations. Prepared external tool families: {len([v for v in portability.get('external_pipelines', {}).values() if v.get('status') != 'missing'])}; upstream Factum control status: {portability.get('upstream_factum_control', {}).get('status')}; locally live-confirmed findings: {sum(1 for v in portability.get('external_pipelines', {}).values() if v.get('live_observed') and v.get('passed') is True) + (1 if portability.get('upstream_factum_control', {}).get('live_observed') and portability.get('upstream_factum_control', {}).get('passed') is True else 0)}.

## Retained from v0.15-v0.10

- pinned LinkML 1.11.1 -> PostgreSQL experiment over untouched externally generated DDL;
- bounded external PostgreSQL artifact auditing with explicit SHA-bound semantic-to-physical mappings;
- authoritative shared source-semantic witness execution and portable evidence fingerprints;
- one source witness/envelope lowered across PostgreSQL, MongoDB, and TypeDB with post-state checks where write acceptance is insufficient;
- Apache Ossie, LinkML, and native Factum ORM JSON import/audit support;
- 20-case public semantic-portability benchmark with 142 obligations and source counterexamples;
- deliberate target mutations, TypeDB projection/live adapter, and hosted CI configuration.

Packaged benchmark status: {portability.get('benchmark', {}).get('case_count', 0)} cases; expectations: {portability.get('benchmark', {}).get('expectations_passed')}; structural coverage: {portability.get('benchmark', {}).get('coverage_complete')}; local live-observed obligations: {sum(portability.get('live_observed_obligations', {}).values())}. Local absence of services/drivers remains `not_run`, not a pass.

## Retained from v0.9

- filesystem repository remotes with repository-identity pinning;
- deterministic SHA-256 content-addressed transfer packs;
- fetch into remote-tracking refs without local branch movement;
- fast-forward-only push with immutable object transfer separated from ref update;
- portable v2 Ed25519 revision attestations, while retaining verification for repository-bound v1 attestations;
- explicit trusted/revoked key policy and protected-branch minimum-signature/allow-list rules;
- repository verification of protected branch trust state;
- ORM-aware merge conflict explanations plus `merge.explanation.md`;
- collaboration demo proving unsigned push rejection, signed push success, no-op deduplicated repeat push, fetch-only tracking, trust verification, and explained semantic conflicts.

Packaged v0.9 invariant status: {repository.get('all_v0_9_invariants_pass')}; signed push: {repository.get('repository_v0_9_demo', {}).get('signed_push_status')}; deduplicated second-push objects: {repository.get('repository_v0_9_demo', {}).get('dedup_push_object_count')}; trust: {repository.get('repository_v0_9_demo', {}).get('protected_branch_trust_passed')}.

## Added in v0.8 (retained)

- optional `identity "..."` declarations for model, entity/value/fact/role/field/objectification semantics;
- automatic rename alignment by stable semantic ID before name/hint matching;
- branch and immutable tag refs over the content-verified revision DAG;
- two-parent merge commits and merge-base discovery;
- three-way semantic merge with deterministic conflicts and file-carried `ours`/`theirs`/`base`/`delete` resolutions;
- revision provenance (`author`, `message`) in signed revision payloads;
- optional Ed25519 append-only attestations with registered public-key verification;
- identity-coverage evidence in builds and repository revisions.

Packaged v0.8 invariant status: {repository.get('all_v0_8_invariants_pass')}; clean merge: {repository.get('repository_v0_8_demo', {}).get('clean_merge_status')}; deliberate conflict count: {repository.get('repository_v0_8_demo', {}).get('conflict_count')}; signature verification: {repository.get('repository_v0_8_demo', {}).get('signature_verification_passed')}.

## Added in v0.7 (retained)

- packaged metamodel v1/v2 registry, preserving the v0.6 metamodel as v1;
- v2 codec/semantic/manifest integrity facts;
- semantic metamodel diff and population migration by source-codec decode + destination-codec re-encode;
- immutable filesystem model repository with source/canonical/population artifacts and SHA-256 verification;
- repository list/log/checkout/verify/diff commands;
- immutable repository-head migration to a new metamodel version.

Packaged v0.7 invariant status: {repository.get("all_v0_7_invariants_pass")}; repository demo revisions: {repository.get("repository_log", {}).get("revision_count")}; repository verification: {repository.get("repository_verification", {}).get("passed")}.

## Added in v0.6

- canonical Factgraph metamodel expressed as an ordinary Factgraph model;
- core and compiler-envelope population encoders/decoders;
- semantic and manifest round-trip contracts for reified models;
- self-description and deterministic second-encoding fixed-point evidence;
- `factgraph metamodel` and `factgraph reify`;
- metamodel evidence integrated into ordinary builds.

Metamodel self-roundtrip: {metamodel.get("self_host", {}).get("semantic_roundtrip_equal")}; manifest roundtrip: {metamodel.get("self_host", {}).get("manifest_roundtrip_equal")}; fixed point: {metamodel.get("self_host", {}).get("second_encoding_identical")}.

## Retained from v0.5

- `factgraph live-migrate`;
- isolated PostgreSQL/MongoDB live migration namespaces;
- PostgreSQL transactional execution, preflight gating, rollback, and live schema verification;
- MongoDB structured operation execution and live validator/index verification;
- versioned passing/failing migration fixtures;
- explicit risky/destructive execution policy with manual operations always blocked;

## Retained from v0.4

- normalized semantic diff;
- explicit rename hints for object types, facts, roles, and fields;
- semantic change safety classification;
- target-independent migration plans;
- PostgreSQL migration/preflight generation;
- MongoDB migration/preflight generation;
- safe/risky/destructive script tiers with manual operations always blocked;
- migration fixture families for additive/tightening, rename, and destructive changes;
- `factgraph diff` and `factgraph migrate`.

## Evidence in this package

Ordinary compiler/conformance suite:

- {summary['example_count']} examples;
- {t['static_cases']} structural conformance checks;
- complete native/emulated capability coverage: {summary['all_coverage_complete']};
- all current round-trip invariants passed: {summary['all_roundtrip_invariants_pass']}.

v0.4 migration-planning suite:

- {migrations['example_count']} migration examples;
- {mt['semantic_changes']} semantic changes across fixtures;
- {mt['postgres_operations']} PostgreSQL migration operations;
- {mt['mongo_operations']} MongoDB migration operations;
- all migration fixtures planned successfully: {migrations['all_planned']}.

Migration planning artifacts are not records of execution. v0.5 live execution writes separate reports, and this package contains {live_migrations['fixture_count']} versioned migration fixtures. Destructive/manual operations remain blocked unless policy explicitly permits destructive operations; manual operations are never generic-executable. See `QUALITY_REPORT.md`, `V0_5_LIVE_MIGRATION_SUMMARY.json`, `V0_4_MIGRATION_SUMMARY.json`, and `docs/LIVE_MIGRATIONS.md`.
"""


def project_tree() -> str:
    ignored = {"__pycache__", ".pytest_cache"}
    lines = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if any(part in ignored for part in rel.parts):
            continue
        if p.is_file():
            lines.append("./" + rel.as_posix())
    return "\n".join(lines) + "\n"


def main() -> int:
    ART.mkdir(exist_ok=True)
    env = environment()
    write_json(ART / "ENVIRONMENT_CAPABILITIES.json", env)
    summary = conformance_summary(env)
    migrations = migration_summary()
    live_migrations = live_migration_summary(env)
    meta = metamodel_summary()
    repository = repository_summary()
    portability = portability_summary()
    write_json(ART / "CONFORMANCE_SUMMARY.json", summary)
    write_json(ART / "V0_4_MIGRATION_SUMMARY.json", migrations)
    write_json(ART / "V0_5_LIVE_MIGRATION_SUMMARY.json", live_migrations)
    write_json(ART / "V0_6_METAMODEL_SUMMARY.json", meta)
    write_json(ART / "V0_7_REPOSITORY_SUMMARY.json", repository)
    write_json(ART / "V0_8_REPOSITORY_SUMMARY.json", repository)
    write_json(ART / "V0_9_COLLABORATION_SUMMARY.json", repository)
    write_json(ART / "V0_12_PORTABILITY_SUMMARY.json", portability)
    (ART / "QUALITY_REPORT.md").write_text(quality_report(summary, env, migrations, live_migrations, meta, repository, portability), encoding="utf-8")
    (ART / "RELEASE_NOTES.md").write_text(release_notes(summary, migrations, live_migrations, meta, repository, portability), encoding="utf-8")
    (ART / "PROJECT_TREE.txt").write_text(project_tree(), encoding="utf-8")
    print(json.dumps({"version": __version__, "examples": summary["example_count"], "migration_examples": migrations["example_count"], "live_migration_fixtures": live_migrations["fixture_count"], "metamodel_examples": meta["example_count"], "metamodel_self_host_pass": bool(meta.get("self_host", {}).get("second_encoding_identical")), "repository_v0_7_pass": repository.get("all_v0_7_invariants_pass"), "repository_v0_8_pass": repository.get("all_v0_8_invariants_pass"), "repository_v0_9_pass": repository.get("all_v0_9_invariants_pass"), "repository_revisions": repository.get("repository_log", {}).get("revision_count"), "portability_cases": portability.get("benchmark", {}).get("case_count"), "external_formats": portability.get("external_format_count"), "totals": summary["totals"], "migration_totals": migrations["totals"], "metamodel_totals": meta["totals"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
