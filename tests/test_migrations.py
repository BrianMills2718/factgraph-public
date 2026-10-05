from __future__ import annotations

import json
from pathlib import Path

from factgraph.cli import build_migration
from factgraph.diff import ChangeKind, ChangeSafety, MigrationHints, semantic_diff
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.migrations import postgres as pgm, mongo as mgm

ROOT = Path(__file__).resolve().parents[1]
MIG = ROOT / "examples" / "migrations"


def load(path: Path):
    return normalize_model(parse_model(path.read_text(encoding="utf-8")))


def pair(name: str):
    d = MIG / name
    return load(d / "before.fg"), load(d / "after.fg")


def test_v04_identical_models_have_empty_semantic_diff():
    before, _ = pair("safe_risky")
    d = semantic_diff(before, before)
    assert d.changes == []
    assert d.to_dict()["summary"]["change_count"] == 0


def test_v04_safe_risky_semantic_classification():
    before, after = pair("safe_risky")
    d = semantic_diff(before, after)
    kinds = {c.kind for c in d.changes}
    assert ChangeKind.ADD_FACT in kinds
    assert ChangeKind.ADD_FIELD in kinds
    assert ChangeKind.CHANGE_FIELD_REQUIRED in kinds
    required = next(c for c in d.changes if c.kind == ChangeKind.CHANGE_FIELD_REQUIRED)
    assert required.safety == ChangeSafety.REQUIRES_DATA_CHECK
    added_age = next(c for c in d.changes if c.kind == ChangeKind.ADD_FIELD and c.subject == "Person.age")
    assert added_age.safety == ChangeSafety.SAFE


def test_v04_rename_hints_preserve_semantic_identity_without_guessing():
    before, after = pair("rename")
    hints = MigrationHints.from_json((MIG / "rename" / "hints.json").read_text())
    d = semantic_diff(before, after, hints)
    kinds = {c.kind for c in d.changes}
    assert ChangeKind.RENAME_OBJECT_TYPE in kinds
    assert ChangeKind.RENAME_FIELD in kinds
    assert ChangeKind.RENAME_ROLE in kinds
    assert ChangeKind.DROP_OBJECT_TYPE not in kinds
    assert ChangeKind.ADD_OBJECT_TYPE not in kinds
    assert not d.to_dict()["summary"]["has_destructive"]


def test_v04_unhinted_rename_is_drop_add_and_warned_not_guessed():
    before, after = pair("rename")
    d = semantic_diff(before, after)
    assert any(c.kind == ChangeKind.DROP_OBJECT_TYPE and c.subject == "Customer" for c in d.changes)
    assert any(c.kind == ChangeKind.ADD_OBJECT_TYPE and c.subject == "Client" for c in d.changes)
    assert any("renames" in w for w in d.warnings)


def test_v04_postgres_rename_is_ordered_table_then_column_and_is_safe():
    before, after = pair("rename")
    hints = MigrationHints.from_json((MIG / "rename" / "hints.json").read_text())
    plan = pgm.build_plan(before, after, hints)
    safe = pgm.emit_safe_sql(plan)
    assert safe.index("ALTER TABLE customer RENAME TO client;") < safe.index("ALTER TABLE client RENAME COLUMN email TO primary_email;")
    assert "ALTER TABLE ownership RENAME COLUMN owner_id TO client_id;" in safe
    assert not plan.to_dict()["summary"]["has_destructive"]


def test_v04_postgres_required_and_unique_changes_are_gated_with_preflights():
    before, after = pair("safe_risky")
    plan = pgm.build_plan(before, after)
    assert any(o.kind == "set_not_null" and o.safety == ChangeSafety.REQUIRES_DATA_CHECK for o in plan.operations)
    assert any(o.kind == "add_unique" and o.safety == ChangeSafety.REQUIRES_DATA_CHECK for o in plan.operations)
    preflight = pgm.emit_preflight_sql(plan)
    assert "WHERE display_name IS NULL" in preflight
    assert "HAVING COUNT(*) > 1" in preflight
    safe = pgm.emit_safe_sql(plan)
    assert "-- BLOCKED: ALTER TABLE person ALTER COLUMN display_name SET NOT NULL;" in safe
    risky = pgm.emit_risky_preview_sql(plan)
    assert "ALTER TABLE person ALTER COLUMN display_name SET NOT NULL;" in risky


def test_v04_mongo_nonindexed_field_rename_is_staged_but_fact_role_rename_is_manual():
    before, after = pair("rename")
    hints = MigrationHints.from_json((MIG / "rename" / "hints.json").read_text())
    plan = mgm.build_plan(before, after, hints)
    assert any(o.kind == "rename_field_data" and o.safety == ChangeSafety.REQUIRES_DATA_CHECK for o in plan.operations)
    assert any(o.kind == "rename_indexed_field" and o.safety == ChangeSafety.MANUAL for o in plan.operations)
    safe = mgm.emit_safe_script(plan)
    assert 'renameCollection("client")' in safe
    assert "// BLOCKED:" in safe
    risky = mgm.emit_risky_preview_script(plan)
    assert '"$rename":{"email":"primary_email"}' in risky
    # Manual indexed role rename remains blocked even in risky preview.
    line = next(x for x in risky.splitlines() if '"$rename":{"owner_id":"client_id"}' in x)
    assert line.startswith("// BLOCKED:")


def test_v04_destructive_operations_are_blocked_in_safe_scripts_and_visible_in_preview():
    before, after = pair("destructive")
    pgplan = pgm.build_plan(before, after)
    mplan = mgm.build_plan(before, after)
    assert pgplan.to_dict()["summary"]["has_destructive"]
    assert mplan.to_dict()["summary"]["has_destructive"]
    pgsafe = pgm.emit_safe_sql(pgplan)
    pgdestructive = pgm.emit_destructive_preview_sql(pgplan)
    assert "-- BLOCKED: DROP TABLE legacy_link;" in pgsafe
    assert "DROP TABLE legacy_link;" in pgdestructive
    mgsafe = mgm.emit_safe_script(mplan)
    mgdestructive = mgm.emit_destructive_preview_script(mplan)
    assert "// BLOCKED:" in mgsafe
    assert '.drop();' in mgdestructive


def test_v04_value_domain_tightening_requires_data_check_and_relaxation_is_safe():
    tight_before = load(MIG / "safe_risky" / "after.fg")
    text = (MIG / "safe_risky" / "after.fg").read_text().replace("range(0, 120)", "range(18, 65)")
    tight_after = normalize_model(parse_model(text))
    d = semantic_diff(tight_before, tight_after)
    vc = next(c for c in d.changes if c.details and c.details.get("constraint_kind") == "value")
    assert vc.safety == ChangeSafety.REQUIRES_DATA_CHECK
    relaxed = semantic_diff(tight_after, tight_before)
    vc2 = next(c for c in relaxed.changes if c.details and c.details.get("constraint_kind") == "value")
    assert vc2.safety == ChangeSafety.SAFE


def test_v04_migration_build_writes_file_only_handoff(tmp_path: Path):
    before = MIG / "safe_risky" / "before.fg"
    after = MIG / "safe_risky" / "after.fg"
    summary = build_migration(before, after, tmp_path)
    assert summary["status"] == "planned"
    expected = [
        "semantic_diff.json", "semantic_diff.md", "migration_plan.json", "migration_plan.md",
        "postgres/plan.json", "postgres/migration.sql", "postgres/preflight.sql",
        "mongo/plan.json", "mongo/migration.js", "mongo/preflight.js", "MIGRATION_SUMMARY.json",
        "before/normalized.fg", "after/normalized.fg", "hints.json",
    ]
    for rel in expected:
        assert (tmp_path / rel).is_file(), rel
    parsed = json.loads((tmp_path / "semantic_diff.json").read_text())
    assert parsed["format"] == "factgraph-semantic-diff-v1"


def test_v04_diff_and_target_plans_are_deterministic():
    before, after = pair("safe_risky")
    assert semantic_diff(before, after).to_json() == semantic_diff(before, after).to_json()
    assert pgm.build_plan(before, after).to_json() == pgm.build_plan(before, after).to_json()
    assert mgm.build_plan(before, after).to_json() == mgm.build_plan(before, after).to_json()


def test_v04_invalid_migration_hint_is_rejected():
    before, after = pair("rename")
    hints = MigrationHints.from_dict({"object_types": {"DoesNotExist": "Client"}})
    try:
        semantic_diff(before, after, hints)
    except ValueError as exc:
        assert "unknown before object" in str(exc)
    else:
        raise AssertionError("expected invalid hint to fail")
