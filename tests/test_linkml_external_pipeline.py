from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from factgraph.cli import load_input
from factgraph.postgres_artifact import build_external_cases, mapping_template, validate_mapping

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples" / "external_pipelines" / "linkml_1_11_1"


def test_linkml_external_case_imports_range_without_gaps():
    model, imported = load_input(CASE / "value_range.yaml", "linkml")
    assert imported is not None
    assert imported.report["unsupported_or_lossy_count"] == 0
    value_constraints = [c for c in model.constraints.values() if c.kind.value == "value"]
    assert len(value_constraints) == 1
    assert value_constraints[0].value_spec == {"kind": "range", "min": 0, "max": 130}


def test_linkml_external_case_uses_same_age_minus_one_semantic_witness():
    model, _ = load_input(CASE / "value_range.yaml", "linkml")
    # Representative physical shape expected from the pinned lower-case LinkML case.
    # This is only a mapping/lowering unit test; it is not the third-party runtime evidence.
    sql = """CREATE TABLE person (\n    person_id TEXT NOT NULL,\n    age INTEGER NOT NULL,\n    PRIMARY KEY (person_id)\n);\n"""
    mapping = mapping_template(model, sql)
    assert validate_mapping(model, sql, mapping)["passed"] is True
    case = next(c for c in build_external_cases(model, mapping) if c.kind == "value")
    assert case.expected_write_outcome == "not_asserted"
    assert [op["sql"] for op in case.program.operations] == [
        "INSERT INTO person (person_id, age) VALUES ('v2', -1);"
    ]


def test_linkml_pipeline_runner_is_honest_when_generator_is_unavailable(tmp_path):
    out = tmp_path / "linkml-pipeline"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_linkml_postgres_pipeline_audit.py"),
            "--generator",
            "factgraph-generator-that-does-not-exist",
            "--out-dir",
            str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((out / "SUMMARY.json").read_text())
    assert summary["status"] == "generator_unavailable"
    assert summary["live_observed"] is False
    assert summary["passed"] is None
    assert summary["expected_value_range_observation"] == "weakened"


def test_hosted_ci_pins_real_linkml_release_and_requires_live_external_audit():
    workflow = (ROOT / ".github" / "workflows" / "semantic-portability.yml").read_text()
    assert "third-party-linkml-postgres:" in workflow
    assert "linkml==1.11.1" in workflow
    assert "run_linkml_postgres_pipeline_audit.py" in workflow
    assert "--require-generator --require-live" in workflow
