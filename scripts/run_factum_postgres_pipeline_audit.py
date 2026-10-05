#!/usr/bin/env python3
from __future__ import annotations

import argparse
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

PINNED_FACTUM_VERSION = "0.5.0"
DEFAULT_SOURCE = ROOT / "examples" / "external_pipelines" / "factum_0_5_0" / "mandatory_bridge.orm.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _generator_exists(raw: str) -> str | None:
    path = Path(raw)
    if path.exists():
        return str(path.resolve())
    return shutil.which(raw)


def _runtime_version(package_json: Path) -> str | None:
    try:
        raw = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception:
        return None
    version = raw.get("version")
    return str(version) if isinstance(version, str) else None


def _factum_mapping(model, artifact_sql: str) -> dict[str, Any]:
    """Explicit mapping for the pinned small Factum Rmap experiment.

    The names are not inferred from the SQL text. They are an experiment fixture
    tied to the source model and to Factum 0.5.0's documented Rmap naming for this
    case. Live structural preflight must still prove the generated artifact has
    these exact quoted identifiers before any semantic observation can count.
    """
    mapping = mapping_template(model, artifact_sql)
    entities = {o.name: o for o in model.object_types.values() if isinstance(o, EntityType)}
    person = entities["Person"]
    skill = entities["Skill"]

    p_row = mapping["entities"][person.id]
    p_row["table"] = "Person"
    s_row = mapping["entities"][skill.id]
    s_row["table"] = "Skill"
    for hint in model.field_hints.values():
        if hint.owner_object_type_id == person.id:
            p_row["field_columns"][hint.field_fact_id] = "personNr"
        elif hint.owner_object_type_id == skill.id:
            s_row["field_columns"][hint.field_fact_id] = "skillCode"

    fact = next(f for f in model.fact_types.values() if f.id not in model.field_hints and f.name == "PersonHasSkill")
    f_row = mapping["facts"][fact.id]
    f_row["table"] = "PersonHasSkill"
    for role in fact.roles:
        f_row["role_columns"][role.id] = ["personNr" if role.name == "person" else "skillCode"]
    mapping["experiment_mapping_note"] = (
        "Pinned Factum 0.5.0 Rmap case: quoted tables Person, Skill, PersonHasSkill and identifier columns personNr/skillCode. "
        "The mapping is explicit; live information_schema preflight is authoritative."
    )
    return mapping


def _target_mandatory_result(model, report: dict[str, Any]) -> dict[str, Any] | None:
    for row in report.get("results", []):
        c = model.constraints.get(row.get("obligation_id"))
        if c is None or c.kind != ConstraintKind.MANDATORY or not c.fact_type_id:
            continue
        if c.fact_type_id in model.field_hints:
            continue
        return row
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pinned Factum ORM 0.5.0 -> PostgreSQL semantic audit")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--generator", default=str(ROOT / "node_modules" / ".bin" / "factum"))
    parser.add_argument("--package-json", type=Path, default=ROOT / "node_modules" / "factum-orm" / "package.json")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--postgres-dsn", default=None)
    parser.add_argument("--require-generator", action="store_true")
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    source_text = args.source.read_text(encoding="utf-8")
    (out / "source.orm.json").write_text(source_text, encoding="utf-8")
    provenance = json.loads((DEFAULT_SOURCE.parent / "PROVENANCE.json").read_text(encoding="utf-8"))
    _write_json(out / "PROVENANCE.json", provenance)

    generator = _generator_exists(args.generator)
    runtime_version = _runtime_version(args.package_json)
    generator_prefix = (["node", generator] if generator and Path(generator).suffix == ".js" else [generator or args.generator])
    command = generator_prefix + ["ddl", str(args.source), "--dialect", "postgres"]
    _write_json(out / "generator_plan.json", {
        "package": "factum-orm",
        "pinned_version": PINNED_FACTUM_VERSION,
        "runtime_version": runtime_version,
        "command": command,
        "source": str(args.source),
    })

    if generator is None or runtime_version is None:
        summary = {
            "format": "factgraph-third-party-factum-postgres-audit-v1",
            "status": "generator_unavailable",
            "live_observed": False,
            "passed": None,
            "expected_mandatory_observation": "weakened",
            "actual_mandatory_observation": None,
            "reason": "pinned Factum ORM generator/package is not installed in this environment",
            "pinned_factum_version": PINNED_FACTUM_VERSION,
            "runtime_factum_version": runtime_version,
        }
        _write_json(out / "SUMMARY.json", summary)
        return 2 if (args.require_generator or args.require_live) else 0

    if runtime_version != PINNED_FACTUM_VERSION:
        summary = {
            "format": "factgraph-third-party-factum-postgres-audit-v1",
            "status": "generator_version_mismatch",
            "live_observed": False,
            "passed": None,
            "expected_mandatory_observation": "weakened",
            "actual_mandatory_observation": None,
            "pinned_factum_version": PINNED_FACTUM_VERSION,
            "runtime_factum_version": runtime_version,
            "reason": "refusing to attribute evidence to the pinned external pipeline when a different Factum ORM version is installed",
        }
        _write_json(out / "SUMMARY.json", summary)
        return 2

    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    (out / "generator.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out / "generator.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    _write_json(out / "generator_result.json", {"command": command, "returncode": proc.returncode, "runtime_factum_version": runtime_version})
    if proc.returncode != 0 or not proc.stdout.strip():
        _write_json(out / "SUMMARY.json", {
            "format": "factgraph-third-party-factum-postgres-audit-v1",
            "status": "generator_failed",
            "live_observed": False,
            "passed": False,
            "expected_mandatory_observation": "weakened",
            "actual_mandatory_observation": None,
            "reason": "the pinned third-party generator failed or produced empty stdout",
        })
        return 1

    artifact_sql = proc.stdout
    (out / "generated_by_factum.sql").write_text(artifact_sql, encoding="utf-8")
    model, imported = load_input(args.source, "factum")
    _write_json(out / "source_import_report.json", imported.report if imported is not None else {})
    if imported is not None:
        (out / "normalized.fg").write_text(imported.generated_source, encoding="utf-8")

    mapping = _factum_mapping(model, artifact_sql)
    _write_json(out / "mapping.json", mapping)
    validation = validate_mapping(model, artifact_sql, mapping)
    _write_json(out / "mapping_validation.json", validation)
    if not validation["passed"]:
        _write_json(out / "SUMMARY.json", {
            "format": "factgraph-third-party-factum-postgres-audit-v1",
            "status": "mapping_contract_mismatch",
            "live_observed": False,
            "passed": False,
            "expected_mandatory_observation": "weakened",
            "actual_mandatory_observation": None,
            "mapping_errors": validation["errors"],
            "reason": "the pinned Factum output no longer fits the explicit bounded external-layout contract",
        })
        return 1

    audit_report = write_bundle(model, artifact_sql, mapping, out / "factgraph_audit", dsn=args.postgres_dsn)
    target = _target_mandatory_result(model, audit_report)
    actual = (target.get("refined_observation") or target.get("observed_preservation")) if target else None
    live = audit_report.get("status") == "completed"
    expected = "weakened"
    passed = (actual == expected) if live else None
    summary = {
        "format": "factgraph-third-party-factum-postgres-audit-v1",
        "status": audit_report.get("status"),
        "live_observed": live,
        "passed": passed,
        "pinned_factum_version": PINNED_FACTUM_VERSION,
        "runtime_factum_version": runtime_version,
        "expected_mandatory_observation": expected,
        "actual_mandatory_observation": actual,
        "mandatory_obligation_id": target.get("obligation_id") if target else None,
        "artifact_sha256": audit_report.get("artifact_sha256"),
        "mapping_sha256": audit_report.get("mapping_sha256"),
        "case_plan_sha256": audit_report.get("case_plan_sha256"),
        "acceptance_probe_plan_sha256": audit_report.get("acceptance_probe_plan_sha256"),
        "conclusion_contract": (
            "A live 'weakened' result means PostgreSQL realized the source-invalid Person-without-PersonHasSkill population in the untouched Factum-generated relational artifact. "
            "If the invalid witness is blocked, the source-valid participation probe is also executed before the result can be refined to preserved_on_tested_cases; rejection of that valid probe is stronger_or_incompatible evidence."
        ),
    }
    _write_json(out / "SUMMARY.json", summary)
    if args.require_live and not live:
        return 2
    if live and passed is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
