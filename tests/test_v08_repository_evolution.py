from __future__ import annotations

import json
from pathlib import Path

import pytest

from factgraph.cli import main
from factgraph.diff import semantic_diff
from factgraph.identity import identity_report
from factgraph.merge import semantic_merge
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.printer import print_model
from factgraph.repository import (
    commit_model_text,
    create_branch,
    create_tag,
    init_repository,
    list_refs,
    load_revision_model,
    merge_refs,
    sign_revision,
    verify_repository,
    verify_signatures,
)
from factgraph.signing import generate_keypair


BASE = '''model TeamApp identity "team-app" {
  value UserId: UUID identity "user-id"
  value TeamId: UUID identity "team-id"
  value Text: String identity "text"

  entity User identity "user" {
    id userId: UserId identity "user.id"
  }
  entity Team identity "team" {
    id teamId: TeamId identity "team.id"
  }

  fact Membership identity "membership"(
    member: User identity "membership.member",
    team: Team identity "membership.team"
  ) {
    reading "{member} belongs to {team}"
    unique(member, team)
  }
}
'''

OURS = BASE.replace(
    'id userId: UserId identity "user.id"',
    'id userId: UserId identity "user.id"\n    displayName: Text? identity "user.display-name"',
)

THEIRS = (
    BASE.replace('value UserId: UUID identity "user-id"', 'value PersonId: UUID identity "user-id"')
    .replace('entity User identity "user"', 'entity Person identity "user"')
    .replace('id userId: UserId identity "user.id"', 'id personId: PersonId identity "user.id"')
    .replace('member: User identity', 'member: Person identity')
)


def model(text: str):
    return normalize_model(parse_model(text))


def test_explicit_identity_survives_rename_and_diff_needs_no_hints():
    before = model(BASE)
    after = model(THEIRS)
    assert before.id == after.id
    assert before.object_type_by_name("User").id == after.object_type_by_name("Person").id
    assert before.object_type_by_name("UserId").id == after.object_type_by_name("PersonId").id
    assert before.fact_by_name("Membership").role("member").id == after.fact_by_name("Membership").role("member").id

    diff = semantic_diff(before, after).to_dict()
    assert diff["warnings"] == []
    assert diff["summary"]["by_kind"]["rename_object_type"] == 2
    assert diff["summary"]["by_kind"]["rename_field"] == 1
    assert "drop_object_type" not in diff["summary"]["by_kind"]

    canonical = print_model(after)
    assert 'entity Person identity "user"' in canonical
    reparsed = model(canonical)
    assert reparsed.semantically_equal(after)
    assert reparsed.field_hints == after.field_hints
    coverage = identity_report(after)
    assert coverage["stable"] > coverage["legacy_name_derived"]


def test_branches_tags_provenance_and_clean_semantic_merge(tmp_path: Path):
    repo = tmp_path / "repo"
    init_repository(repo, name="V08Repo")
    base = commit_model_text(repo, BASE, model_key="team-app", provenance={"author": "Ada", "message": "base"})
    create_branch(repo, "team-app", "feature/person", from_ref="main")
    create_tag(repo, "team-app", "v1", ref="main")

    ours = commit_model_text(repo, OURS, model_key="team-app", branch="main", provenance={"author": "Ada", "message": "display name"})
    theirs = commit_model_text(repo, THEIRS, model_key="team-app", branch="feature/person", provenance={"author": "Lin", "message": "rename user"})
    refs = list_refs(repo, "team-app")
    assert refs["tags"]["v1"] == base["revision_id"]
    assert refs["branches"]["main"] == ours["revision_id"]
    assert refs["branches"]["feature/person"] == theirs["revision_id"]

    planned = merge_refs(repo, "team-app", ours="main", theirs="feature/person")
    assert planned["merge"]["status"] == "merged"
    assert planned["merge"]["summary"]["conflict_count"] == 0

    committed = merge_refs(
        repo,
        "team-app",
        ours="main",
        theirs="feature/person",
        commit=True,
        author="Merge Bot",
        message="merge person rename",
    )
    meta = committed["commit"]
    assert meta["parent_revision_ids"] == [ours["revision_id"], theirs["revision_id"]]
    assert meta["provenance"] == {"author": "Merge Bot", "message": "merge person rename"}
    merged = load_revision_model(repo, "team-app", "main")
    assert merged.object_type_by_name("Person")
    person = merged.object_type_by_name("Person")
    field_names = sorted(h.field_name for h in merged.field_hints.values() if h.owner_object_type_id == person.id)
    assert field_names == ["displayName", "personId"]
    verification = verify_repository(repo)
    assert verification["passed"] is True, verification["errors"]


def test_semantic_merge_reports_and_resolves_modify_modify_conflict():
    base = model(BASE)
    ours = model(BASE.replace('value Text: String identity "text"', 'value Text: Int identity "text"'))
    theirs = model(BASE.replace('value Text: String identity "text"', 'value Text: Bool identity "text"'))
    result = semantic_merge(base, ours, theirs)
    assert result.status == "conflicts"
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.kind == "object_type"

    resolved = semantic_merge(base, ours, theirs, {conflict.id: "ours"})
    assert resolved.status == "merged"
    assert resolved.merged_model is not None
    assert resolved.merged_model.object_type_by_name("Text").scalar_kind == "Int"
    assert resolved.applied_resolutions[conflict.id] == "ours"


def test_revision_attestation_verifies_and_tampering_is_detected(tmp_path: Path):
    pytest.importorskip("cryptography")
    repo = tmp_path / "repo"
    init_repository(repo, name="SignedRepo")
    commit = commit_model_text(repo, BASE, model_key="team-app")
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    generate_keypair(private, public)
    attestation = sign_revision(repo, "team-app", ref="main", private_key_path=private, signer="Test Signer")
    assert attestation["revision_id"] == commit["revision_id"]
    assert verify_signatures(repo)["passed"] is True
    assert verify_repository(repo)["passed"] is True

    att_path = repo / attestation["attestation_path"]
    data = json.loads(att_path.read_text())
    data["signature"] = "A" * len(data["signature"])
    att_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sig_report = verify_signatures(repo)
    assert sig_report["passed"] is False
    assert verify_repository(repo)["passed"] is False


def test_v08_repository_cli_writes_branch_merge_and_signature_handoffs(tmp_path: Path):
    pytest.importorskip("cryptography")
    repo = tmp_path / "repo"
    init_file = tmp_path / "init.json"
    assert main(["repo-init", str(repo), "--name", "CliV08", "--out", str(init_file)]) == 0
    base_file = tmp_path / "base.fg"
    ours_file = tmp_path / "ours.fg"
    theirs_file = tmp_path / "theirs.fg"
    base_file.write_text(BASE)
    ours_file.write_text(OURS)
    theirs_file.write_text(THEIRS)

    c1 = tmp_path / "c1.json"
    assert main(["repo-commit", str(repo), str(base_file), "--model-key", "team-app", "--author", "Ada", "--out", str(c1)]) == 0
    branch_out = tmp_path / "branch.json"
    assert main(["repo-branch", str(repo), "team-app", "feature", "--out", str(branch_out)]) == 0
    c2 = tmp_path / "c2.json"
    c3 = tmp_path / "c3.json"
    assert main(["repo-commit", str(repo), str(ours_file), "--model-key", "team-app", "--branch", "main", "--out", str(c2)]) == 0
    assert main(["repo-commit", str(repo), str(theirs_file), "--model-key", "team-app", "--branch", "feature", "--out", str(c3)]) == 0

    merge_dir = tmp_path / "merge"
    assert main(["repo-merge", str(repo), "team-app", "--ours", "main", "--theirs", "feature", "--commit", "--out-dir", str(merge_dir)]) == 0
    assert (merge_dir / "merge.json").exists()
    assert (merge_dir / "merged.fg").exists()

    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    key_out = tmp_path / "key.json"
    assert main(["repo-keygen", "--private-key", str(private), "--public-key", str(public), "--out", str(key_out)]) == 0
    sig_out = tmp_path / "sig.json"
    assert main(["repo-sign", str(repo), "team-app", "--ref", "main", "--private-key", str(private), "--signer", "CLI", "--out", str(sig_out)]) == 0
    verify_out = tmp_path / "sig-verify.json"
    assert main(["repo-verify-signatures", str(repo), "--out", str(verify_out)]) == 0
    assert json.loads(verify_out.read_text())["passed"] is True
