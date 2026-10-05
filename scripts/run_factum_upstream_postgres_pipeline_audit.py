#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.cli import load_input  # noqa: E402
from factgraph.model import ConstraintKind, EntityType  # noqa: E402
from factgraph.postgres_artifact import mapping_template, validate_mapping, write_bundle  # noqa: E402

FACTUM_COMMIT = "7897dd0c4303b9eea46c60342f1632b27a624744"
FACTUM_VERSION = "0.5.0"
FACTUM_PACKAGE_BLOB = "2079186fbc29b4d1214f3acb732bc0ccb99355cf"
FACTUM_LOCK_BLOB = "c6543f0df885f36880c5f7d7904addb090b95882"
FACTUM_CLI_BLOB = "b6fa78285c3635e8f8119bd6fbf6bf5db3dd63e6"
MODEL_COMMIT = "e5990315e9a2a7905d129c5626cbde09dab2a842"
MODEL_TREE = "0436823ffda532782557cfc049fa834c04da8870"
MODEL_PATH = Path("models/fig-mandatory.orm.json")
MODEL_BLOB = "dab575088c3642a26d4b6850095726a86d48ed0e"
MODEL_SHA256 = "f5111e58c806674b262d582d9bc4ddd7dd5b41c9a0b33a24b37d814a6bace807"
PROVENANCE = ROOT / "examples" / "external_pipelines" / "factum_0_5_0" / "upstream_fig_mandatory" / "PROVENANCE.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _git(repo: Path, *args: str) -> str | None:
    if not (repo / ".git").exists() or shutil.which("git") is None:
        return None
    proc = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _package_version(path: Path) -> str | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = raw.get("version")
    return str(value) if isinstance(value, str) else None


def verify_checkouts(factum_repo: Path, model_repo: Path) -> dict[str, Any]:
    generator = factum_repo / "bin" / "factum.js"
    source = model_repo / MODEL_PATH
    package = factum_repo / "package.json"
    lock = factum_repo / "package-lock.json"
    checks = {
        "factum_checkout_exists": factum_repo.is_dir(),
        "model_checkout_exists": model_repo.is_dir(),
        "node_available": shutil.which("node") is not None,
        "factum_head": _git(factum_repo, "rev-parse", "HEAD"),
        "expected_factum_head": FACTUM_COMMIT,
        "factum_package_blob": _git(factum_repo, "rev-parse", "HEAD:package.json"),
        "expected_factum_package_blob": FACTUM_PACKAGE_BLOB,
        "factum_lock_blob": _git(factum_repo, "rev-parse", "HEAD:package-lock.json"),
        "expected_factum_lock_blob": FACTUM_LOCK_BLOB,
        "factum_cli_blob": _git(factum_repo, "rev-parse", "HEAD:bin/factum.js"),
        "expected_factum_cli_blob": FACTUM_CLI_BLOB,
        "factum_runtime_version": _package_version(package),
        "expected_factum_version": FACTUM_VERSION,
        "model_head": _git(model_repo, "rev-parse", "HEAD"),
        "expected_model_head": MODEL_COMMIT,
        "model_tree": _git(model_repo, "rev-parse", "HEAD^{tree}"),
        "expected_model_tree": MODEL_TREE,
        "model_blob": _git(model_repo, "rev-parse", f"HEAD:{MODEL_PATH.as_posix()}"),
        "expected_model_blob": MODEL_BLOB,
        "model_sha256": _sha256(source),
        "expected_model_sha256": MODEL_SHA256,
        "generator_exists": generator.is_file(),
        "source_exists": source.is_file(),
    }
    required_pairs = [
        (checks["factum_head"], FACTUM_COMMIT),
        (checks["factum_package_blob"], FACTUM_PACKAGE_BLOB),
        (checks["factum_lock_blob"], FACTUM_LOCK_BLOB),
        (checks["factum_cli_blob"], FACTUM_CLI_BLOB),
        (checks["factum_runtime_version"], FACTUM_VERSION),
        (checks["model_head"], MODEL_COMMIT),
        (checks["model_tree"], MODEL_TREE),
        (checks["model_blob"], MODEL_BLOB),
        (checks["model_sha256"], MODEL_SHA256),
    ]
    checkouts_present = factum_repo.is_dir() and model_repo.is_dir() and generator.is_file() and source.is_file()
    checks["checkouts_present"] = checkouts_present
    checks["passed"] = bool(checkouts_present and checks["node_available"] and all(actual == expected for actual, expected in required_pairs))
    return checks


def upstream_mapping(model, artifact_sql: str) -> dict[str, Any]:
    """Explicit Factum Rmap mapping for upstream fig-mandatory.orm.json.

    Both functional binaries are absorbed into Person. Nothing is inferred from
    the SQL text; live information_schema preflight remains authoritative.
    """
    mapping = mapping_template(model, artifact_sql)
    entities = {o.name: o for o in model.object_types.values() if isinstance(o, EntityType)}
    person = entities["Person"]
    company = entities["Company"]
    mapping["entities"][person.id]["table"] = "Person"
    mapping["entities"][company.id]["table"] = "Company"
    for hint in model.field_hints.values():
        if hint.owner_object_type_id == person.id:
            mapping["entities"][person.id]["field_columns"][hint.field_fact_id] = "personNr"
        elif hint.owner_object_type_id == company.id:
            mapping["entities"][company.id]["field_columns"][hint.field_fact_id] = "companyName"

    facts = {f.name: f for f in model.fact_types.values() if f.id not in model.field_hints}
    for fact_name, non_anchor_column in (("works", "companyName"), ("nickname", "nickname")):
        fact = facts[fact_name]
        row = mapping["facts"][fact.id]
        anchor = next(r for r in fact.roles if r.player_id == person.id)
        other = next(r for r in fact.roles if r.id != anchor.id)
        row["storage_mode"] = "absorbed"
        row["anchor_role_id"] = anchor.id
        row["table"] = "Person"
        row["role_columns"][anchor.id] = ["personNr"]
        row["role_columns"][other.id] = [non_anchor_column]
    mapping["experiment_mapping_note"] = (
        "Factum upstream fig-mandatory Rmap control: works and nickname are functional binaries explicitly mapped as absorbed columns on Person. "
        "This is mapping metadata supplied by the experiment; live physical-structure verification must match the untouched generated DDL before evidence counts."
    )
    return mapping


def _works_mandatory_result(model, report: dict[str, Any]) -> dict[str, Any] | None:
    for row in report.get("results", []):
        c = model.constraints.get(row.get("obligation_id"))
        if c is None or c.kind != ConstraintKind.MANDATORY or not c.fact_type_id:
            continue
        fact = model.fact_types.get(c.fact_type_id)
        if fact is not None and fact.name == "works":
            return row
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit the exact upstream Factum 0.5.0 fig-mandatory model through its pinned PostgreSQL generator")
    parser.add_argument("--factum-repo", type=Path, default=ROOT / ".third_party" / "factum-orm")
    parser.add_argument("--model-repo", type=Path, default=ROOT / ".third_party" / "factum-book-models")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--postgres-dsn", default=None)
    parser.add_argument("--require-checkouts", action="store_true")
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "PROVENANCE.json", json.loads(PROVENANCE.read_text(encoding="utf-8")))
    verification = verify_checkouts(args.factum_repo, args.model_repo)
    _write_json(out / "SOURCE_PROVENANCE_VERIFICATION.json", verification)

    if not verification["checkouts_present"]:
        _write_json(out / "SUMMARY.json", {
            "format": "factgraph-third-party-factum-upstream-postgres-audit-v1",
            "status": "upstream_checkouts_unavailable",
            "live_observed": False,
            "passed": None,
            "expected_mandatory_observation": "preserved_on_tested_cases",
            "actual_mandatory_observation": None,
            "reason": "the exact pinned Factum generator/model checkouts are not present in this environment",
            "provenance_verification_passed": False,
        })
        return 2 if (args.require_checkouts or args.require_live) else 0
    if not verification["passed"]:
        _write_json(out / "SUMMARY.json", {
            "format": "factgraph-third-party-factum-upstream-postgres-audit-v1",
            "status": "provenance_mismatch",
            "live_observed": False,
            "passed": False,
            "expected_mandatory_observation": "preserved_on_tested_cases",
            "actual_mandatory_observation": None,
            "reason": "one or more generator/source commit, blob, version, hash, or runtime checks did not match the pinned experiment",
            "provenance_verification_passed": False,
        })
        return 1

    source = args.model_repo / MODEL_PATH
    generator = args.factum_repo / "bin" / "factum.js"
    # Source is copied into the evidence bundle only at run time; it is not
    # vendored into the Factgraph source distribution.
    (out / "upstream_source.orm.json").write_bytes(source.read_bytes())
    command = ["node", str(generator), "ddl", str(source), "--dialect", "postgres"]
    _write_json(out / "generator_plan.json", {"command": command, "cwd": str(args.factum_repo), "provenance_verified": True})
    proc = subprocess.run(command, cwd=args.factum_repo, text=True, capture_output=True)
    (out / "generator.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out / "generator.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    _write_json(out / "generator_result.json", {"returncode": proc.returncode, "command": command})
    if proc.returncode != 0 or not proc.stdout.strip():
        _write_json(out / "SUMMARY.json", {
            "format": "factgraph-third-party-factum-upstream-postgres-audit-v1",
            "status": "generator_failed",
            "live_observed": False,
            "passed": False,
            "expected_mandatory_observation": "preserved_on_tested_cases",
            "actual_mandatory_observation": None,
            "provenance_verification_passed": True,
        })
        return 1

    artifact_sql = proc.stdout
    (out / "generated_by_factum.sql").write_text(artifact_sql, encoding="utf-8")
    model, imported = load_input(source, "factum")
    _write_json(out / "source_import_report.json", imported.report if imported is not None else {})
    if imported is not None:
        (out / "normalized.fg").write_text(imported.generated_source, encoding="utf-8")

    mapping = upstream_mapping(model, artifact_sql)
    _write_json(out / "mapping.json", mapping)
    validation = validate_mapping(model, artifact_sql, mapping)
    _write_json(out / "mapping_validation.json", validation)
    if not validation["passed"]:
        _write_json(out / "SUMMARY.json", {
            "format": "factgraph-third-party-factum-upstream-postgres-audit-v1",
            "status": "mapping_contract_mismatch",
            "live_observed": False,
            "passed": False,
            "expected_mandatory_observation": "preserved_on_tested_cases",
            "actual_mandatory_observation": None,
            "mapping_errors": validation["errors"],
            "provenance_verification_passed": True,
        })
        return 1

    report = write_bundle(model, artifact_sql, mapping, out / "factgraph_audit", dsn=args.postgres_dsn)
    target = _works_mandatory_result(model, report)
    actual = (target.get("refined_observation") or target.get("observed_preservation")) if target else None
    live = report.get("status") == "completed"
    expected = "preserved_on_tested_cases"
    passed = (actual == expected) if live else None
    _write_json(out / "SUMMARY.json", {
        "format": "factgraph-third-party-factum-upstream-postgres-audit-v1",
        "status": report.get("status"),
        "live_observed": live,
        "passed": passed,
        "expected_mandatory_observation": expected,
        "actual_mandatory_observation": actual,
        "mandatory_obligation_id": target.get("obligation_id") if target else None,
        "provenance_verification_passed": True,
        "artifact_sha256": report.get("artifact_sha256"),
        "mapping_sha256": report.get("mapping_sha256"),
        "case_plan_sha256": report.get("case_plan_sha256"),
        "acceptance_probe_plan_sha256": report.get("acceptance_probe_plan_sha256"),
        "conclusion_contract": (
            "This is an upstream-authored preservation control. The invalid Person-without-works population must be prevented and the generated source-valid works participation probe must be accepted before the tested case is classified preserved_on_tested_cases. "
            "The result remains finite-probe evidence, not general Factum/PostgreSQL equivalence."
        ),
    })
    if args.require_live and not live:
        return 2
    if live and passed is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
