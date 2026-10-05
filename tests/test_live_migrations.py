from __future__ import annotations

import json
from pathlib import Path

from factgraph.cli import main
from factgraph.diff import ChangeSafety
from factgraph.live.migration_common import MigrationExecutionPolicy, load_fixture, operation_selected, plan_expected_complete
from factgraph.live.postgres_migration import evaluate_preflight
from factgraph.migrations import mongo as mgm, postgres as pgm
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "examples" / "migrations"


def load(path: Path):
    return normalize_model(parse_model(path.read_text(encoding="utf-8")))


def pair(name: str):
    d = MIG / name
    return load(d / "before.fg"), load(d / "after.fg")


def test_v05_execution_policy_never_selects_manual_and_gates_risky_destructive():
    before, after = pair("destructive")
    plan = pgm.build_plan(before, after)
    safe = MigrationExecutionPolicy()
    destructive = MigrationExecutionPolicy(allow_destructive=True)
    for op in plan.operations:
        if op.safety == ChangeSafety.MANUAL:
            assert not operation_selected(op, destructive)
        if op.safety == ChangeSafety.DESTRUCTIVE:
            assert not operation_selected(op, safe)
            assert operation_selected(op, destructive)


def test_v05_safe_risky_plan_is_complete_only_when_risky_is_approved():
    before, after = pair("safe_risky")
    pg = pgm.build_plan(before, after)
    mg = mgm.build_plan(before, after)
    assert not plan_expected_complete(pg, MigrationExecutionPolicy())
    assert not plan_expected_complete(mg, MigrationExecutionPolicy())
    assert plan_expected_complete(pg, MigrationExecutionPolicy(allow_risky=True))
    assert plan_expected_complete(mg, MigrationExecutionPolicy(allow_risky=True))


def test_v05_mongo_planner_carries_structured_execution_parameters():
    before, after = pair("safe_risky")
    plan = mgm.build_plan(before, after)
    create = next(o for o in plan.operations if o.kind == "create_collection")
    assert create.parameters["collection"] == "invitation"
    assert create.parameters["spec"]["name"] == "invitation"
    unique = next(o for o in plan.operations if o.kind == "create_index" and o.safety == ChangeSafety.REQUIRES_DATA_CHECK)
    assert unique.parameters["collection"] == "membership"
    assert unique.parameters["index"]["unique"] is True
    validator = next(o for o in plan.operations if o.kind == "update_validator" and o.target_object == "person")
    assert "display_name" in validator.parameters["validator"]["$jsonSchema"]["required"]


def test_v05_postgres_preflight_interpretation_distinguishes_zero_and_violations():
    before, after = pair("safe_risky")
    plan = pgm.build_plan(before, after)
    null_op = next(o for o in plan.operations if o.kind == "set_not_null")
    assert evaluate_preflight(null_op, [(0,)], ["SELECT ..."])["passed"] is True
    assert evaluate_preflight(null_op, [(2,)], ["SELECT ..."])["passed"] is False
    unique_op = next(o for o in plan.operations if o.kind == "add_unique")
    assert evaluate_preflight(unique_op, [], ["SELECT ..."])["passed"] is True
    assert evaluate_preflight(unique_op, [("p1", 2)], ["SELECT ..."])["passed"] is False


def test_v05_migration_fixtures_are_versioned_and_cover_pass_and_fail_populations():
    passing = load_fixture(MIG / "safe_risky" / "fixture.pass.json")
    failing = load_fixture(MIG / "safe_risky" / "fixture.fail.json")
    assert passing["format"] == "factgraph-live-migration-fixture-v1"
    assert failing["format"] == "factgraph-live-migration-fixture-v1"
    assert len(passing["postgres"]["setup_sql"]) == 3
    assert len(failing["postgres"]["setup_sql"]) == 5
    assert len(failing["mongo"]["setup"]) == 5


def test_v05_live_migrate_cli_without_services_writes_file_only_not_run_report(tmp_path: Path):
    d = MIG / "safe_risky"
    out = tmp_path / "live_migration"
    rc = main([
        "live-migrate", str(d / "before.fg"), str(d / "after.fg"),
        "--fixture", str(d / "fixture.pass.json"),
        "--allow-risky", "--out-dir", str(out),
    ])
    assert rc == 0
    for name in ["postgres.json", "mongo.json", "LIVE_MIGRATION.json"]:
        assert (out / name).is_file()
    summary = json.loads((out / "LIVE_MIGRATION.json").read_text())
    assert summary["reports"]["postgres"]["status"] == "not_run"
    assert summary["reports"]["mongo"]["status"] == "not_run"
    assert summary["failed_targets"] == []


def test_v05_migrate_planning_artifact_explicitly_records_live_execution_not_run(tmp_path: Path):
    d = MIG / "safe_risky"
    rc = main(["migrate", str(d / "before.fg"), str(d / "after.fg"), "--out-dir", str(tmp_path)])
    assert rc == 0
    status = json.loads((tmp_path / "live_execution_status.json").read_text())
    assert status["status"] == "not_run"
    assert "live-migrate" in status["reason"]
