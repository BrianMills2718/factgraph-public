from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from factgraph import repository as repo
from factgraph import remote
from factgraph.cli import main
from factgraph.merge import semantic_merge
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model


ROOT = Path(__file__).resolve().parents[1]
V08 = ROOT / "examples" / "repository_v08"


def model(text: str):
    return normalize_model(parse_model(text))


def deterministic_keypair(private: Path, public: Path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def test_content_addressed_push_and_fetch_do_not_conflate_fetch_with_branch_advance(tmp_path: Path):
    local = tmp_path / "local"
    hub = tmp_path / "hub"
    repo.init_repository(local, name="local")
    repo.init_repository(hub, name="hub")
    base = repo.commit_model(local, V08 / "base.fg", model_key="team")
    remote.add_remote(local, "origin", hub)

    pushed = remote.push(local, "origin", "team", out_dir=tmp_path / "push")
    assert pushed["status"] == "pushed"
    pack = pushed["pack"]
    assert pack["revision_ids"] == [base["revision_id"]]
    assert pack["object_count"] > 0
    for row in pack["files"]:
        obj = tmp_path / "push" / "pack" / "objects" / "sha256" / row["sha256"][:2] / row["sha256"]
        assert hashlib.sha256(obj.read_bytes()).hexdigest() == row["sha256"]

    remote_edit = repo.commit_model(hub, V08 / "main.fg", model_key="team")
    fetched = remote.fetch(local, "origin", "team", out_dir=tmp_path / "fetch")
    assert fetched["status"] == "fetched"
    refs = repo.list_refs(local, "team")
    assert refs["branches"]["main"] == base["revision_id"]
    assert refs["remote_tracking"]["origin"]["main"] == remote_edit["revision_id"]
    assert repo.load_revision_model(local, "team", remote_edit["revision_id"]).manifest_dict() == repo.load_revision_model(hub, "team", "main").manifest_dict()
    assert repo.verify_repository(local)["passed"] is True
    assert repo.verify_repository(hub)["passed"] is True


def test_protected_remote_push_requires_portable_trusted_attestation_and_revocation_is_local_policy(tmp_path: Path):
    local = tmp_path / "local"
    hub = tmp_path / "hub"
    repo.init_repository(local, name="local")
    repo.init_repository(hub, name="hub")
    first = repo.commit_model(local, V08 / "base.fg", model_key="team")
    remote.add_remote(local, "origin", hub)

    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    deterministic_keypair(private, public)
    trusted = repo.trust_public_key(hub, public, label="release key")
    repo.protect_branch(hub, "team", "main", min_valid_signatures=1, allowed_key_ids=[trusted["key_id"]])

    with pytest.raises(remote.RemoteError, match="trust policy rejected"):
        remote.push(local, "origin", "team", out_dir=tmp_path / "unsigned")
    # Immutable objects may arrive, but the protected branch must not move.
    assert repo.list_refs(hub, "team")["branches"].get("main") is None

    att = repo.sign_revision(local, "team", ref="main", private_key_path=private, signer="release")
    assert att["format"] == "factgraph-revision-attestation-v2"
    pushed = remote.push(local, "origin", "team", out_dir=tmp_path / "signed")
    assert pushed["status"] == "pushed"
    trust = repo.evaluate_revision_trust(hub, "team", branch="main")
    assert trust["passed"] is True
    assert trust["accepted_signature_count"] == 1
    assert repo.list_refs(hub, "team")["branches"]["main"] == first["revision_id"]

    # Trust is a local repository decision, not something transferred with the key.
    repo.revoke_trusted_key(hub, trusted["key_id"], reason="rotation")
    trust2 = repo.evaluate_revision_trust(hub, "team", branch="main")
    assert trust2["passed"] is False
    assert repo.verify_repository(hub)["passed"] is False


def test_push_is_fast_forward_only(tmp_path: Path):
    local = tmp_path / "local"
    hub = tmp_path / "hub"
    repo.init_repository(local, name="local")
    repo.init_repository(hub, name="hub")
    repo.commit_model(local, V08 / "base.fg", model_key="team")
    remote.add_remote(local, "origin", hub)
    remote.push(local, "origin", "team", out_dir=tmp_path / "basepush")

    repo.commit_model(local, V08 / "main.fg", model_key="team")
    repo.commit_model(hub, V08 / "feature_person.fg", model_key="team")
    hub_before = repo.resolve_revision(hub, "team", "main")
    with pytest.raises(remote.RemoteError, match="non-fast-forward"):
        remote.push(local, "origin", "team", out_dir=tmp_path / "diverged")
    assert repo.resolve_revision(hub, "team", "main") == hub_before


def test_merge_conflicts_explain_orm_semantic_impact():
    base = model((V08 / "base.fg").read_text())
    ours = model((V08 / "conflict_ours.fg").read_text())
    theirs = model((V08 / "conflict_theirs.fg").read_text())
    result = semantic_merge(base, ours, theirs)
    assert result.status == "conflicts"
    conflict = result.conflicts[0]
    assert conflict.explanation
    assert conflict.explanation["orm_term"] == "object type"
    assert conflict.explanation["semantic_impact"] == "population semantics"
    assert "scalar_kind" in conflict.explanation["overlapping_changed_fields"]
    markdown = result.to_markdown()
    assert "# Semantic merge explanation" in markdown
    assert "Object Type: Text" in markdown
    assert "Which meaning of object type" in markdown


def test_v09_cli_writes_remote_trust_and_merge_explanation_files(tmp_path: Path):
    local = tmp_path / "local"
    hub = tmp_path / "hub"
    assert main(["repo-init", str(local), "--name", "local", "--out", str(tmp_path / "local.init.json")]) == 0
    assert main(["repo-init", str(hub), "--name", "hub", "--out", str(tmp_path / "hub.init.json")]) == 0
    commit = tmp_path / "commit.json"
    assert main(["repo-commit", str(local), str(V08 / "base.fg"), "--model-key", "team", "--out", str(commit)]) == 0
    configured = tmp_path / "remote.json"
    assert main(["repo-remote", str(local), "origin", str(hub), "--out", str(configured)]) == 0
    push_dir = tmp_path / "push"
    assert main(["repo-push", str(local), "origin", "team", "--out-dir", str(push_dir)]) == 0
    assert (push_dir / "transfer.json").exists()
    assert (push_dir / "pack" / "pack.json").exists()

    # Build a conflict in one repository and ensure the human explanation is a file.
    assert main(["repo-branch", str(local), "team", "ours", "--from-ref", "main", "--out", str(tmp_path / "bo.json")]) == 0
    assert main(["repo-branch", str(local), "team", "theirs", "--from-ref", "main", "--out", str(tmp_path / "bt.json")]) == 0
    assert main(["repo-commit", str(local), str(V08 / "conflict_ours.fg"), "--model-key", "team", "--branch", "ours", "--out", str(tmp_path / "co.json")]) == 0
    assert main(["repo-commit", str(local), str(V08 / "conflict_theirs.fg"), "--model-key", "team", "--branch", "theirs", "--out", str(tmp_path / "ct.json")]) == 0
    merge_dir = tmp_path / "merge"
    assert main(["repo-merge", str(local), "team", "--ours", "ours", "--theirs", "theirs", "--out-dir", str(merge_dir)]) == 2
    assert (merge_dir / "merge.explanation.md").exists()
    assert "population semantics" in (merge_dir / "merge.explanation.md").read_text()

def test_v09_still_verifies_legacy_repository_bound_v1_attestations(tmp_path: Path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from factgraph import signing

    root = tmp_path / "repo"
    repo.init_repository(root, name="legacy-signature")
    commit = repo.commit_model(root, V08 / "base.fg", model_key="team")
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    public = key.public_key()
    key_id = signing.key_id_from_public_key(public)
    key_path = root / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    payload = repo._revision_signature_payload_v1(root, "team", commit["revision_id"])
    canonical = signing.canonical_json_bytes(payload)
    attestation = {
        "format": "factgraph-revision-attestation-v1",
        "algorithm": "ed25519",
        "key_id": key_id,
        "signer": "legacy",
        "model_key": "team",
        "revision_id": commit["revision_id"],
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature": __import__("base64").b64encode(key.sign(canonical)).decode("ascii"),
    }
    path = repo._attestation_dir(root, "team", commit["revision_id"]) / f"{key_id.replace(':', '--')}.json"
    repo._write(path, json.dumps(attestation, indent=2, sort_keys=True) + "\n")
    assert repo.verify_signatures(root)["passed"] is True

def test_v09_trust_policy_cli_roundtrip(tmp_path: Path):
    root = tmp_path / "repo"
    assert main(["repo-init", str(root), "--name", "trust-cli", "--out", str(tmp_path / "init.json")]) == 0
    assert main(["repo-commit", str(root), str(V08 / "base.fg"), "--model-key", "team", "--out", str(tmp_path / "commit.json")]) == 0
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    deterministic_keypair(private, public)
    trusted_out = tmp_path / "trusted.json"
    assert main(["repo-trust-key", str(root), str(public), "--label", "release", "--out", str(trusted_out)]) == 0
    key_id = json.loads(trusted_out.read_text())["key_id"]
    protected = tmp_path / "protected.json"
    assert main(["repo-protect-branch", str(root), "team", "main", "--min-signatures", "1", "--key-id", key_id, "--out", str(protected)]) == 0
    evaluation = tmp_path / "eval-before.json"
    assert main(["repo-trust-evaluate", str(root), "team", "--ref", "main", "--branch", "main", "--out", str(evaluation)]) == 1
    assert main(["repo-sign", str(root), "team", "--ref", "main", "--private-key", str(private), "--out", str(tmp_path / "sig.json")]) == 0
    evaluation2 = tmp_path / "eval-after.json"
    assert main(["repo-trust-evaluate", str(root), "team", "--ref", "main", "--branch", "main", "--out", str(evaluation2)]) == 0
    policy = tmp_path / "policy.json"
    assert main(["repo-trust-policy", str(root), "--out", str(policy)]) == 0
    assert key_id in json.loads(policy.read_text())["keys"]

def test_remote_pack_skips_nonportable_v1_attestations(tmp_path: Path):
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from factgraph import signing

    source, dest = tmp_path / "source", tmp_path / "dest"
    repo.init_repository(source, name="source")
    repo.init_repository(dest, name="dest")
    commit = repo.commit_model(source, V08 / "base.fg", model_key="team")
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    public = key.public_key()
    key_id = signing.key_id_from_public_key(public)
    key_path = source / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    payload = repo._revision_signature_payload_v1(source, "team", commit["revision_id"])
    canonical = signing.canonical_json_bytes(payload)
    attestation = {
        "format": "factgraph-revision-attestation-v1",
        "algorithm": "ed25519",
        "key_id": key_id,
        "signer": "legacy",
        "model_key": "team",
        "revision_id": commit["revision_id"],
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "signature": __import__("base64").b64encode(key.sign(canonical)).decode("ascii"),
    }
    path = repo._attestation_dir(source, "team", commit["revision_id"]) / f"{key_id.replace(':', '--')}.json"
    repo._write(path, json.dumps(attestation, indent=2, sort_keys=True) + "\n")

    remote.add_remote(source, "origin", dest)
    pushed = remote.push(source, "origin", "team", out_dir=tmp_path / "push")
    assert pushed["status"] == "pushed"
    assert pushed["pack"]["skipped_nonportable_attestations"] == [str(path.relative_to(source)).replace("\\", "/")]
    assert repo.verify_signatures(dest)["attestation_count"] == 0
    assert repo.verify_repository(dest)["passed"] is True
