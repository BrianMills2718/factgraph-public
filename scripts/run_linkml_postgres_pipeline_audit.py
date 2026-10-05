#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.cli import load_input  # noqa: E402
from factgraph.postgres_artifact import mapping_template, validate_mapping, write_bundle  # noqa: E402

PINNED_LINKML_VERSION = "1.11.1"
DEFAULT_SOURCE = ROOT / "examples" / "external_pipelines" / "linkml_1_11_1" / "value_range.yaml"
RELEASE_URL = "https://github.com/linkml/linkml/releases/tag/v1.11.1"
GENERATOR_DOC_URL = "https://linkml.io/linkml/generators/sqltable.html"
GENERATOR_SOURCE_URL = (
    "https://raw.githubusercontent.com/linkml/linkml/v1.11.1/"
    "packages/linkml/src/linkml/generators/sqltablegen.py"
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _linkml_version() -> str | None:
    try:
        return importlib.metadata.version("linkml")
    except importlib.metadata.PackageNotFoundError:
        return None


def _value_result(report: dict[str, Any]) -> dict[str, Any] | None:
    rows = [row for row in report.get("results", []) if row.get("kind") == "value"]
    return rows[0] if len(rows) == 1 else None


def _provenance(*, source: Path, generator: str, runtime_version: str | None, command: list[str]) -> dict[str, Any]:
    return {
        "format": "factgraph-third-party-pipeline-provenance-v1",
        "pipeline": "linkml-sqltablegen-postgresql",
        "third_party_project": "LinkML",
        "pinned_package": f"linkml=={PINNED_LINKML_VERSION}",
        "runtime_package_version": runtime_version,
        "generator_executable": generator,
        "generator_command": command,
        "source_file": source.name,
        "source_format": "linkml",
        "target": "postgresql",
        "release_url": RELEASE_URL,
        "generator_documentation_url": GENERATOR_DOC_URL,
        "generator_source_url": GENERATOR_SOURCE_URL,
        "independence_contract": (
            "The PostgreSQL DDL is emitted by the pinned LinkML generator process and is then treated as an opaque "
            "external artifact. Factgraph does not call its own PostgreSQL emitter to create or repair this DDL."
        ),
        "hypothesis": (
            "The source LinkML age slot carries minimum_value=0 and maximum_value=130. The experiment asks whether "
            "the independently generated PostgreSQL artifact prevents the same Factgraph source-semantic age=-1 witness."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate PostgreSQL DDL with pinned LinkML, then audit that untouched artifact with Factgraph semantic witnesses"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--generator", default="gen-sqltables")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "external_pipelines" / "linkml_1_11_1")
    parser.add_argument("--postgres-dsn", default=os.environ.get("FACTGRAPH_POSTGRES_DSN"))
    parser.add_argument("--require-generator", action="store_true")
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args(argv)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    source_text = args.source.read_text(encoding="utf-8")
    (out / "source.yaml").write_text(source_text, encoding="utf-8")

    command = [args.generator, "--dialect", "postgresql", str(args.source)]
    runtime_version = _linkml_version()
    provenance = _provenance(
        source=args.source,
        generator=args.generator,
        runtime_version=runtime_version,
        command=command,
    )
    _write_json(out / "PROVENANCE.json", provenance)

    generator_path = shutil.which(args.generator)
    if generator_path is None or runtime_version is None:
        summary = {
            "format": "factgraph-third-party-linkml-postgres-audit-v1",
            "status": "generator_unavailable",
            "live_observed": False,
            "passed": None,
            "expected_value_range_observation": "weakened",
            "actual_value_range_observation": None,
            "reason": "pinned LinkML generator/package is not installed in this environment",
            "pinned_linkml_version": PINNED_LINKML_VERSION,
            "runtime_linkml_version": runtime_version,
        }
        _write_json(out / "SUMMARY.json", summary)
        return 2 if (args.require_generator or args.require_live) else 0

    if runtime_version != PINNED_LINKML_VERSION:
        summary = {
            "format": "factgraph-third-party-linkml-postgres-audit-v1",
            "status": "generator_version_mismatch",
            "live_observed": False,
            "passed": None,
            "expected_value_range_observation": "weakened",
            "actual_value_range_observation": None,
            "pinned_linkml_version": PINNED_LINKML_VERSION,
            "runtime_linkml_version": runtime_version,
            "reason": "refusing to attribute evidence to the pinned external pipeline when a different LinkML version is installed",
        }
        _write_json(out / "SUMMARY.json", summary)
        return 2

    proc = subprocess.run(command, text=True, capture_output=True)
    (out / "generator.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out / "generator.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    _write_json(out / "generator_result.json", {
        "command": command,
        "returncode": proc.returncode,
        "runtime_linkml_version": runtime_version,
    })
    if proc.returncode != 0:
        summary = {
            "format": "factgraph-third-party-linkml-postgres-audit-v1",
            "status": "generator_failed",
            "live_observed": False,
            "passed": False,
            "expected_value_range_observation": "weakened",
            "actual_value_range_observation": None,
            "reason": "the pinned third-party generator exited non-zero",
        }
        _write_json(out / "SUMMARY.json", summary)
        return 1

    artifact_sql = proc.stdout
    if not artifact_sql.strip():
        summary = {
            "format": "factgraph-third-party-linkml-postgres-audit-v1",
            "status": "generator_failed",
            "live_observed": False,
            "passed": False,
            "expected_value_range_observation": "weakened",
            "actual_value_range_observation": None,
            "reason": "the pinned third-party generator produced empty stdout",
        }
        _write_json(out / "SUMMARY.json", summary)
        return 1

    generated_path = out / "generated_by_linkml.sql"
    generated_path.write_text(artifact_sql, encoding="utf-8")

    model, imported = load_input(args.source, "linkml")
    _write_json(out / "source_import_report.json", imported.report if imported is not None else {})
    if imported is not None:
        (out / "normalized.fg").write_text(imported.generated_source, encoding="utf-8")

    mapping = mapping_template(model, artifact_sql)
    _write_json(out / "mapping.json", mapping)
    validation = validate_mapping(model, artifact_sql, mapping)
    _write_json(out / "mapping_validation.json", validation)
    if not validation["passed"]:
        summary = {
            "format": "factgraph-third-party-linkml-postgres-audit-v1",
            "status": "mapping_contract_mismatch",
            "live_observed": False,
            "passed": False,
            "expected_value_range_observation": "weakened",
            "actual_value_range_observation": None,
            "mapping_errors": validation["errors"],
            "reason": "the pinned LinkML output no longer fits the bounded explicit v0.14 external-layout contract",
        }
        _write_json(out / "SUMMARY.json", summary)
        return 1

    audit_report = write_bundle(
        model,
        artifact_sql,
        mapping,
        out / "factgraph_audit",
        dsn=args.postgres_dsn,
    )
    value_result = _value_result(audit_report)
    actual = (value_result.get("refined_observation") or value_result.get("observed_preservation")) if value_result else None
    live = audit_report.get("status") == "completed"
    expected = "weakened"
    passed = (actual == expected) if live else None
    summary = {
        "format": "factgraph-third-party-linkml-postgres-audit-v1",
        "status": audit_report.get("status"),
        "live_observed": live,
        "passed": passed,
        "pinned_linkml_version": PINNED_LINKML_VERSION,
        "runtime_linkml_version": runtime_version,
        "expected_value_range_observation": expected,
        "actual_value_range_observation": actual,
        "value_obligation_id": value_result.get("obligation_id") if value_result else None,
        "artifact_sha256": audit_report.get("artifact_sha256"),
        "mapping_sha256": audit_report.get("mapping_sha256"),
        "case_plan_sha256": audit_report.get("case_plan_sha256"),
        "conclusion_contract": (
            "A live 'weakened' result means the source-invalid age=-1 population was realized in the untouched LinkML-generated "
            "PostgreSQL artifact. Generated SQL text alone is never counted as the semantic observation."
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
