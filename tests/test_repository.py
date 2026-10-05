from __future__ import annotations

import json
from pathlib import Path

from factgraph.cli import main
from factgraph.diff import MigrationHints
from factgraph.metamodel import (
    CURRENT_METAMODEL_VERSION,
    SUPPORTED_METAMODEL_VERSIONS,
    decode_model,
    encode_model,
    metamodel_semantic_diff,
    metamodel_source,
    migration_report,
    population_dict,
    population_from_dict,
)
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.printer import print_model
from factgraph.repository import (
    checkout_revision,
    commit_model,
    diff_revisions,
    init_repository,
    list_models,
    log_model,
    migrate_repository_metamodel,
    verify_repository,
)

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path):
    return normalize_model(parse_model(path.read_text(encoding="utf-8")))


def test_versioned_metamodel_sources_and_semantic_diff_are_explicit():
    assert SUPPORTED_METAMODEL_VERSIONS == ("1", "2")
    assert CURRENT_METAMODEL_VERSION == "2"
    assert "MM_ModelCodecVersion" not in metamodel_source("1")
    assert "MM_ModelCodecVersion" in metamodel_source("2")
    diff = metamodel_semantic_diff("1", "2").to_dict()
    assert diff["summary"]["by_kind"]["add_fact"] == 3
    assert diff["summary"]["has_destructive"] is False


def test_v1_population_migrates_to_v2_via_decode_reencode_with_manifest_equality():
    model = load(ROOT / "examples" / "employment.fg")
    v1 = encode_model(model, include_envelope=True, metamodel_version="1")
    migrated, report = migration_report(v1, target_version="2", include_envelope=True)
    assert report["source_version"] == "1"
    assert report["target_version"] == "2"
    assert report["semantic_roundtrip_equal"] is True
    assert report["manifest_roundtrip_equal"] is True
    assert report["migration_passed"] is True
    recovered = decode_model(migrated, include_envelope=True, metamodel_version="2")
    assert recovered.manifest_dict() == model.manifest_dict()
    assert population_dict(migrated)["metamodel_version"] == "2"


def test_v2_population_integrity_fact_detects_tampering():
    model = load(ROOT / "examples" / "warehouse.fg")
    populated = encode_model(model, include_envelope=True, metamodel_version="2")
    data = population_dict(populated)
    # Tamper the modeled name while leaving v2's semantic/manifest integrity
    # rows unchanged. The schema is still structurally valid, but decode must
    # reject the semantic hash mismatch.
    for block in data["facts"]:
        if block["fact"] == "MM_ModelName":
            block["rows"][0][1] = "TamperedWarehouse"
            break
    tampered = population_from_dict(data)
    try:
        decode_model(tampered, include_envelope=True, metamodel_version="2")
    except ValueError as exc:
        assert "integrity hash" in str(exc)
    else:
        raise AssertionError("tampered v2 population unexpectedly decoded")


def test_repository_history_verification_diff_checkout_and_metamodel_migration(tmp_path: Path):
    repo = tmp_path / "repo"
    init_repository(repo, name="TestRepository", default_metamodel_version="1")
    base = ROOT / "examples" / "migrations" / "safe_risky"
    first = commit_model(repo, base / "before.fg", model_key="team-app", metamodel_version="1")
    second = commit_model(repo, base / "after.fg", model_key="team-app", metamodel_version="1")
    assert first["status"] == "committed"
    assert second["parent_revision_id"] == first["revision_id"]

    listed = list_models(repo)
    assert listed["models"][0]["revision_count"] == 2
    assert verify_repository(repo)["passed"] is True

    diff = diff_revisions(repo, "team-app", first["revision_id"], second["revision_id"], hints=MigrationHints.empty())
    assert diff.to_dict()["summary"]["change_count"] > 0

    checkout = tmp_path / "checkout"
    checkout_revision(repo, "team-app", checkout, revision_id=first["revision_id"])
    assert (checkout / "normalized.fg").exists()
    assert print_model(load(base / "before.fg")) == (checkout / "normalized.fg").read_text(encoding="utf-8")

    migrated = migrate_repository_metamodel(repo, target_version="2", model_key="team-app")
    assert migrated["repository_verification_passed"] is True
    assert migrated["results"][0]["status"] == "migrated"
    assert migrated["results"][0]["manifest_roundtrip_equal"] is True
    log = log_model(repo, "team-app")
    assert len(log["revisions"]) == 3
    assert log["revisions"][-1]["metamodel_version"] == "2"
    assert log["revisions"][-1]["operation"] == "metamodel_migration"
    assert verify_repository(repo)["passed"] is True


def test_repository_verification_detects_artifact_tampering(tmp_path: Path):
    repo = tmp_path / "repo"
    init_repository(repo, name="TamperTest")
    commit = commit_model(repo, ROOT / "examples" / "warehouse.fg", model_key="warehouse")
    entry = list_models(repo)["models"][0]
    rev_dir = repo / "models" / entry["directory"] / "revisions" / commit["revision_id"]
    (rev_dir / "semantic.json").write_text("{}\n", encoding="utf-8")
    report = verify_repository(repo)
    assert report["passed"] is False
    assert any("artifact hash mismatch: semantic.json" in e for e in report["errors"])


def test_repository_cli_is_file_oriented(tmp_path: Path):
    repo = tmp_path / "repo"
    init_out = tmp_path / "init.json"
    assert main(["repo-init", str(repo), "--name", "CliRepo", "--metamodel-version", "1", "--out", str(init_out)]) == 0
    assert init_out.exists()

    c1 = tmp_path / "commit1.json"
    assert main(["repo-commit", str(repo), str(ROOT / "examples" / "warehouse.fg"), "--model-key", "warehouse", "--metamodel-version", "1", "--out", str(c1)]) == 0
    first = json.loads(c1.read_text())

    verify1 = tmp_path / "verify1.json"
    assert main(["repo-verify", str(repo), "--out", str(verify1)]) == 0
    assert json.loads(verify1.read_text())["passed"] is True

    migrate = tmp_path / "migrate.json"
    assert main(["repo-migrate-metamodel", str(repo), "--to-version", "2", "--model-key", "warehouse", "--out", str(migrate)]) == 0
    mig = json.loads(migrate.read_text())
    assert mig["repository_verification_passed"] is True

    log_out = tmp_path / "log.json"
    assert main(["repo-log", str(repo), "warehouse", "--out", str(log_out)]) == 0
    log = json.loads(log_out.read_text())
    assert log["revisions"][0]["revision_id"] == first["revision_id"]
    assert len(log["revisions"]) == 2

    diff_dir = tmp_path / "diff"
    assert main(["repo-diff", str(repo), "warehouse", log["revisions"][0]["revision_id"], log["revisions"][1]["revision_id"], "--out-dir", str(diff_dir)]) == 0
    # Metamodel-only re-encoding changes repository representation, not the
    # represented domain model, so the semantic model diff is empty.
    semantic_diff_json = json.loads((diff_dir / "semantic_diff.json").read_text())
    assert semantic_diff_json["summary"]["change_count"] == 0

    checkout = tmp_path / "checkout"
    assert main(["repo-checkout", str(repo), "warehouse", "--out-dir", str(checkout)]) == 0
    assert (checkout / "CHECKOUT.json").exists()

def test_versioned_metamodel_cli_writes_diff_and_population_migration_handoffs(tmp_path: Path):
    versions = tmp_path / "versions.json"
    assert main(["metamodel-versions", "--out", str(versions)]) == 0
    version_data = json.loads(versions.read_text())
    assert version_data["current_version"] == "2"
    assert [x["version"] for x in version_data["versions"]] == ["1", "2"]

    diff_dir = tmp_path / "metamodel-diff"
    assert main(["metamodel-diff", "1", "2", "--out-dir", str(diff_dir)]) == 0
    assert {"semantic_diff.json", "semantic_diff.md", "from.metamodel.fg", "to.metamodel.fg", "SUMMARY.json"}.issubset(
        {p.name for p in diff_dir.iterdir()}
    )

    reify_dir = tmp_path / "reify-v1"
    assert main([
        "reify", str(ROOT / "examples" / "warehouse.fg"),
        "--metamodel-version", "1", "--out-dir", str(reify_dir)
    ]) == 0
    migrate_dir = tmp_path / "migrate"
    assert main([
        "metamodel-migrate", str(reify_dir / "envelope.population.json"),
        "--to-version", "2", "--out-dir", str(migrate_dir)
    ]) == 0
    report = json.loads((migrate_dir / "migration.json").read_text())
    assert report["migration_passed"] is True
    assert report["manifest_roundtrip_equal"] is True
