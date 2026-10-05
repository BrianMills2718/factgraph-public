from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from factgraph.cli import load_input
from factgraph.model import ConstraintKind, EntityType
from factgraph.postgres_artifact import build_external_acceptance_cases, build_external_cases, mapping_template, validate_mapping

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples" / "external_pipelines" / "factum_0_5_0"


def _factum_mapping(model, sql: str):
    mapping = mapping_template(model, sql)
    entities = {o.name: o for o in model.object_types.values() if isinstance(o, EntityType)}
    for hint in model.field_hints.values():
        if hint.owner_object_type_id == entities["Person"].id:
            mapping["entities"][entities["Person"].id]["field_columns"][hint.field_fact_id] = "personNr"
        if hint.owner_object_type_id == entities["Skill"].id:
            mapping["entities"][entities["Skill"].id]["field_columns"][hint.field_fact_id] = "skillCode"
    mapping["entities"][entities["Person"].id]["table"] = "Person"
    mapping["entities"][entities["Skill"].id]["table"] = "Skill"
    fact = next(f for f in model.fact_types.values() if f.id not in model.field_hints and f.name == "PersonHasSkill")
    mapping["facts"][fact.id]["table"] = "PersonHasSkill"
    for role in fact.roles:
        mapping["facts"][fact.id]["role_columns"][role.id] = ["personNr" if role.name == "person" else "skillCode"]
    return mapping


def test_factum_external_case_imports_total_participation_without_gaps():
    model, imported = load_input(CASE / "mandatory_bridge.orm.json", "factum")
    assert imported is not None
    assert imported.report["unsupported_or_lossy_count"] == 0
    target = [
        c for c in model.constraints.values()
        if c.kind == ConstraintKind.MANDATORY and c.fact_type_id not in model.field_hints
    ]
    assert len(target) == 1


def test_case_sensitive_external_mapping_quotes_factum_identifiers_and_keeps_same_mandatory_witness():
    model, _ = load_input(CASE / "mandatory_bridge.orm.json", "factum")
    sql = '''CREATE TABLE "Person" ("personNr" INTEGER NOT NULL, PRIMARY KEY ("personNr"));
CREATE TABLE "Skill" ("skillCode" TEXT NOT NULL, PRIMARY KEY ("skillCode"));
CREATE TABLE "PersonHasSkill" ("personNr" INTEGER NOT NULL, "skillCode" TEXT NOT NULL, PRIMARY KEY ("personNr", "skillCode"));
'''
    mapping = _factum_mapping(model, sql)
    assert validate_mapping(model, sql, mapping)["passed"] is True
    target = next(
        c for c in build_external_cases(model, mapping)
        if c.kind == "mandatory" and model.constraints[c.obligation_id].fact_type_id not in model.field_hints
    )
    assert [op["sql"] for op in target.program.operations] == [
        'INSERT INTO "Person" ("personNr") VALUES (101);'
    ]


def test_factum_total_participation_has_source_valid_positive_probe_on_same_external_layout():
    model, _ = load_input(CASE / "mandatory_bridge.orm.json", "factum")
    sql = '''CREATE TABLE "Person" ("personNr" INTEGER NOT NULL, PRIMARY KEY ("personNr"));
CREATE TABLE "Skill" ("skillCode" TEXT NOT NULL, PRIMARY KEY ("skillCode"));
CREATE TABLE "PersonHasSkill" ("personNr" INTEGER NOT NULL, "skillCode" TEXT NOT NULL, PRIMARY KEY ("personNr", "skillCode"));
'''
    mapping = _factum_mapping(model, sql)
    target = next(
        c for c in build_external_acceptance_cases(model, mapping)
        if c.kind == "mandatory" and model.constraints[c.obligation_id].fact_type_id not in model.field_hints
    )
    assert target.label.startswith("source-valid participation")
    statements = [op["sql"] for op in target.program.operations]
    assert any(stmt.startswith('INSERT INTO "Person"') for stmt in statements)
    assert any(stmt.startswith('INSERT INTO "Skill"') for stmt in statements)
    assert any(stmt.startswith('INSERT INTO "PersonHasSkill"') for stmt in statements)


def test_factum_pipeline_runner_is_honest_when_generator_is_unavailable(tmp_path):
    out = tmp_path / "factum-pipeline"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_factum_postgres_pipeline_audit.py"),
            "--generator", str(tmp_path / "missing-factum"),
            "--package-json", str(tmp_path / "missing-package.json"),
            "--out-dir", str(out),
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
    assert summary["expected_mandatory_observation"] == "weakened"


def test_hosted_ci_pins_factum_0_5_0_and_requires_live_external_audit():
    workflow = (ROOT / ".github" / "workflows" / "semantic-portability.yml").read_text()
    assert "third-party-factum-postgres:" in workflow
    assert "ref: 7897dd0c4303b9eea46c60342f1632b27a624744" in workflow
    assert "run_factum_postgres_pipeline_audit.py" in workflow
    assert "--require-generator --require-live" in workflow


def test_factum_guid_backed_obligation_ids_use_bounded_audit_filenames(tmp_path):
    from factgraph import audit as semantic_audit

    model, _ = load_input(CASE / "mandatory_bridge.orm.json", "factum")
    out = tmp_path / "audit"
    semantic_audit.write_audit_bundle(model, out, targets=("postgres",))
    generated = [p for p in (out / "witnesses").rglob("*") if p.is_file()]
    assert generated
    assert all(len(p.name.encode("utf-8")) < 180 for p in generated)
    # The semantic ID itself is preserved in the index/artifact payload; only
    # the filesystem component is bounded and hashed.
    index = json.loads((out / "witnesses" / "index.json").read_text())
    assert any("guid" in row["obligation_id"] for row in index)


def test_upstream_factum_provenance_pins_exact_generator_and_model_objects():
    provenance = json.loads((CASE / "upstream_fig_mandatory" / "PROVENANCE.json").read_text())
    assert provenance["generator"]["version"] == "0.5.0"
    assert provenance["generator"]["commit"] == "7897dd0c4303b9eea46c60342f1632b27a624744"
    assert provenance["generator"]["bundled_cli_git_blob_sha1"] == "b6fa78285c3635e8f8119bd6fbf6bf5db3dd63e6"
    assert provenance["source_model"]["commit"] == "e5990315e9a2a7905d129c5626cbde09dab2a842"
    assert provenance["source_model"]["git_blob_sha1"] == "dab575088c3642a26d4b6850095726a86d48ed0e"
    assert provenance["source_model"]["sha256"] == "f5111e58c806674b262d582d9bc4ddd7dd5b41c9a0b33a24b37d814a6bace807"
    assert provenance["expected_live_hypothesis"] == "preserved_on_tested_cases"


def test_upstream_factum_runner_is_honest_when_exact_checkouts_are_unavailable(tmp_path):
    out = tmp_path / "upstream-factum"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_factum_upstream_postgres_pipeline_audit.py"),
            "--factum-repo", str(tmp_path / "missing-generator"),
            "--model-repo", str(tmp_path / "missing-models"),
            "--out-dir", str(out),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads((out / "SUMMARY.json").read_text())
    verification = json.loads((out / "SOURCE_PROVENANCE_VERIFICATION.json").read_text())
    assert summary["status"] == "upstream_checkouts_unavailable"
    assert summary["live_observed"] is False
    assert summary["passed"] is None
    assert summary["expected_mandatory_observation"] == "preserved_on_tested_cases"
    assert verification["passed"] is False


def test_hosted_ci_uses_exact_factum_commits_without_npm_registry_for_factum():
    workflow = (ROOT / ".github" / "workflows" / "semantic-portability.yml").read_text()
    assert "repository: Volland/factum-orm" in workflow
    assert "ref: 7897dd0c4303b9eea46c60342f1632b27a624744" in workflow
    assert "repository: Volland/factum-book-models" in workflow
    assert "ref: e5990315e9a2a7905d129c5626cbde09dab2a842" in workflow
    assert "run_factum_upstream_postgres_pipeline_audit.py" in workflow
    assert "--require-checkouts --require-live" in workflow
    assert "npm install --no-save factum-orm@0.5.0" not in workflow
    assert ".third_party/factum-orm/bin/factum.js" in workflow
