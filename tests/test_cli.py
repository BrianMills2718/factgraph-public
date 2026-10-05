from pathlib import Path
import json

from factgraph.cli import build

ROOT = Path(__file__).resolve().parents[1]


def test_build_writes_every_artifact_to_files(tmp_path):
    out = tmp_path / "build"
    summary = build(ROOT / "examples" / "employment.fg", out)
    expected = [
        "normalized.fg", "semantic.json", "manifest.json", "validation.json", "analyses.json", "incidence.json", "BUILD_SUMMARY.json",
        "postgres/model.sql", "postgres/plan.json", "postgres/capabilities.json", "postgres/structure_recovery.json", "postgres/semantic.json",
        "mongo/model.js", "mongo/spec.json", "mongo/plan.json", "mongo/capabilities.json", "mongo/structure_recovery.json", "mongo/semantic.json",
        "graphql/schema.graphql", "graphql/capabilities.json", "graphql/semantic.json",
        "roundtrip.json",
        "conformance/postgres/cases.json", "conformance/mongo/cases.json",
        "conformance/coverage.json", "conformance/static_results.json", "conformance/live_status.json",
    ]
    for rel in expected:
        assert (out / rel).is_file(), rel
    saved = json.loads((out / "BUILD_SUMMARY.json").read_text())
    assert saved["semantic_roundtrip"]["postgres_with_sidecar"] is True
    assert saved["semantic_roundtrip"]["mongo_with_sidecar"] is True
    assert saved["conformance"]["coverage_complete"] is True
    assert saved["conformance"]["structural_cases_pass"] is True
    assert saved["roundtrip_invariants_pass"] is True
    assert summary == saved


def test_live_conformance_without_services_records_not_run(tmp_path):
    from factgraph.cli import main
    out = tmp_path / "live"
    rc = main(["live-conformance", str(ROOT / "examples" / "warehouse.fg"), "--out-dir", str(out)])
    assert rc == 0
    summary = json.loads((out / "LIVE_CONFORMANCE.json").read_text())
    assert summary["reports"]["postgres"]["status"] == "not_run"
    assert summary["reports"]["mongo"]["status"] == "not_run"


def test_diff_and_migrate_cli_write_file_artifacts(tmp_path):
    from factgraph.cli import main
    mig = ROOT / "examples" / "migrations" / "rename"
    diff_out = tmp_path / "diff"
    rc = main([
        "diff", str(mig / "before.fg"), str(mig / "after.fg"),
        "--hints", str(mig / "hints.json"), "--out-dir", str(diff_out),
    ])
    assert rc == 0
    assert (diff_out / "semantic_diff.json").is_file()
    assert (diff_out / "semantic_diff.md").is_file()

    migrate_out = tmp_path / "migrate"
    rc = main([
        "migrate", str(mig / "before.fg"), str(mig / "after.fg"),
        "--hints", str(mig / "hints.json"), "--out-dir", str(migrate_out),
    ])
    assert rc == 0
    assert (migrate_out / "postgres" / "migration.sql").is_file()
    assert (migrate_out / "mongo" / "migration.js").is_file()
    assert (migrate_out / "MIGRATION_SUMMARY.json").is_file()
