from __future__ import annotations

"""Filesystem remote transport with content-addressed transfer packs.

v0.9 deliberately implements one transport only: another Factgraph repository
reachable through the local filesystem. The pack format is transport-neutral:
logical repository files are mapped to SHA-256-addressed blobs and verified
before immutable revision data is installed.

Fetch never advances a local branch. Push is fast-forward-only and evaluates
the *remote repository's* protected-branch trust policy before moving the
remote branch ref.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
from typing import Any, Iterable

from . import repository as repo


REMOTES_FORMAT = "factgraph-repository-remotes-v1"
PACK_FORMAT = "factgraph-transfer-pack-v1"
TRANSFER_FORMAT = "factgraph-remote-transfer-v1"


class RemoteError(repo.RepositoryError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remotes_path(root: Path) -> Path:
    return Path(root) / "remotes.json"


def _load_remotes(root: Path) -> dict[str, Any]:
    root = Path(root)
    repo._repo_config(root)
    path = _remotes_path(root)
    if not path.exists():
        return {"format": REMOTES_FORMAT, "remotes": {}}
    data = repo._read_json(path)
    if data.get("format") != REMOTES_FORMAT:
        raise RemoteError("invalid remotes.json format")
    data.setdefault("remotes", {})
    return data


def _write_remotes(root: Path, data: dict[str, Any]) -> None:
    payload = {
        "format": REMOTES_FORMAT,
        "remotes": dict(sorted(data.get("remotes", {}).items())),
    }
    repo._write(_remotes_path(Path(root)), _json(payload))


def _stored_remote_path(root: Path, remote_path: Path) -> str:
    # Relative paths make cloned/copyable collaboration fixtures portable and
    # keep deterministic release builds independent of temporary root names.
    rel = os.path.relpath(Path(remote_path).resolve(), start=Path(root).resolve())
    return Path(rel).as_posix()


def _resolved_remote_path(root: Path, stored: str) -> Path:
    p = Path(stored)
    if p.is_absolute():
        return p
    return (Path(root) / p).resolve()


def add_remote(root: Path, name: str, remote_path: Path) -> dict[str, Any]:
    root = Path(root)
    repo._repo_config(root)
    name = repo._validate_ref_name(name)
    remote_path = Path(remote_path)
    remote_config = repo._repo_config(remote_path)
    data = _load_remotes(root)
    existing = data["remotes"].get(name)
    entry = {
        "transport": "filesystem",
        "path": _stored_remote_path(root, remote_path),
        "repository_id": remote_config["repository_id"],
        "repository_name": remote_config["name"],
    }
    if existing is not None and existing != entry:
        raise RemoteError(f"remote {name!r} already points somewhere else")
    data["remotes"][name] = entry
    _write_remotes(root, data)
    return {"format": REMOTES_FORMAT, "name": name, **entry, "status": "configured"}


def remove_remote(root: Path, name: str) -> dict[str, Any]:
    root = Path(root)
    name = repo._validate_ref_name(name)
    data = _load_remotes(root)
    if name not in data["remotes"]:
        raise RemoteError(f"unknown remote {name!r}")
    old = data["remotes"].pop(name)
    _write_remotes(root, data)
    return {"format": REMOTES_FORMAT, "name": name, **old, "status": "removed"}


def list_remotes(root: Path) -> dict[str, Any]:
    return _load_remotes(Path(root))


def resolve_remote(root: Path, name: str) -> Path:
    data = _load_remotes(root)
    if name not in data["remotes"]:
        raise RemoteError(f"unknown remote {name!r}")
    entry = data["remotes"][name]
    if entry.get("transport") != "filesystem":
        raise RemoteError(f"unsupported remote transport {entry.get('transport')!r}")
    path = _resolved_remote_path(Path(root), entry["path"])
    config = repo._repo_config(path)
    if config["repository_id"] != entry.get("repository_id"):
        raise RemoteError(
            f"remote {name!r} repository identity changed: expected {entry.get('repository_id')}, got {config['repository_id']}"
        )
    return path


def _revision_set(root: Path, model_key: str) -> set[str]:
    try:
        return set(repo._model_index(Path(root), model_key).get("revisions", []))
    except repo.RepositoryError:
        return set()


def _revision_closure(root: Path, model_key: str, revision_id: str) -> list[str]:
    """Return ancestors first, selected revision last."""

    root = Path(root)
    seen: set[str] = set()
    order: list[str] = []

    def visit(rid: str) -> None:
        if rid in seen:
            return
        meta = repo._revision_meta(root, model_key, rid)
        for parent in meta.get("parent_revision_ids", []):
            visit(parent)
        seen.add(rid)
        order.append(rid)

    visit(revision_id)
    return order


def _iter_revision_paths(root: Path, model_key: str, revision_ids: Iterable[str]) -> Iterable[Path]:
    for rid in revision_ids:
        directory = repo._revision_dir(root, model_key, rid)
        for p in sorted(directory.rglob("*")):
            if p.is_file():
                yield p


def _iter_attestation_paths(root: Path, model_key: str, reachable: Iterable[str]) -> Iterable[Path]:
    for rid in reachable:
        directory = repo._attestation_dir(root, model_key, rid)
        if directory.exists():
            yield from sorted(p for p in directory.glob("*.json") if p.is_file())


def _pack_logical_files(
    source_root: Path,
    destination_root: Path,
    model_key: str,
    missing_revisions: list[str],
    reachable_revisions: list[str],
) -> tuple[list[Path], list[str]]:
    paths = list(_iter_revision_paths(source_root, model_key, missing_revisions))
    attestation_paths: list[Path] = []
    skipped_nonportable: list[str] = []
    for path in _iter_attestation_paths(source_root, model_key, reachable_revisions):
        try:
            attestation = repo._read_json(path)
        except Exception as exc:
            raise RemoteError(f"cannot package attestation {path}: {exc}") from exc
        if attestation.get("format") != "factgraph-revision-attestation-v2":
            skipped_nonportable.append(path.relative_to(source_root).as_posix())
            continue
        attestation_paths.append(path)
    paths.extend(attestation_paths)
    # Public keys are transferred as cryptographic material only. Their arrival
    # never changes destination trust policy.
    key_ids: set[str] = set()
    for path in attestation_paths:
        try:
            key_ids.add(repo._read_json(path)["key_id"])
        except Exception as exc:
            raise RemoteError(f"cannot package attestation {path}: {exc}") from exc
    for key_id in sorted(key_ids):
        path = source_root / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
        if not path.exists():
            raise RemoteError(f"attestation key {key_id} is missing from source repository")
        paths.append(path)

    # Avoid re-sending immutable/append-only paths whose exact bytes already
    # exist at the destination.
    selected: list[Path] = []
    for path in sorted(set(paths)):
        rel = path.relative_to(source_root)
        dst = destination_root / rel
        if dst.exists():
            if dst.read_bytes() != path.read_bytes():
                raise RemoteError(f"destination already has different immutable bytes at {rel.as_posix()}")
            continue
        selected.append(path)
    return selected, sorted(skipped_nonportable)


def build_pack(
    source_root: Path,
    destination_root: Path,
    model_key: str,
    *,
    ref: str,
    out_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic content-addressed transfer pack."""

    source_root = Path(source_root)
    destination_root = Path(destination_root)
    repo._repo_config(source_root)
    repo._repo_config(destination_root)
    head = repo.resolve_revision(source_root, model_key, ref)
    reachable = _revision_closure(source_root, model_key, head)
    destination_revisions = _revision_set(destination_root, model_key)
    missing = [rid for rid in reachable if rid not in destination_revisions]
    logical_paths, skipped_nonportable = _pack_logical_files(source_root, destination_root, model_key, missing, reachable)

    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    objects_dir = out_dir / "objects" / "sha256"
    objects_dir.mkdir(parents=True, exist_ok=True)
    file_rows: list[dict[str, Any]] = []
    seen_objects: set[str] = set()
    for path in logical_paths:
        rel = path.relative_to(source_root).as_posix()
        data = path.read_bytes()
        digest = _sha(data)
        obj = objects_dir / digest[:2] / digest
        if digest not in seen_objects:
            obj.parent.mkdir(parents=True, exist_ok=True)
            obj.write_bytes(data)
            seen_objects.add(digest)
        file_rows.append({"path": rel, "sha256": digest, "bytes": len(data)})

    payload = {
        "format": PACK_FORMAT,
        "source_repository_id": repo._repo_config(source_root)["repository_id"],
        "destination_repository_id": repo._repo_config(destination_root)["repository_id"],
        "model_key": model_key,
        "ref": ref,
        "head_revision_id": head,
        "reachable_revision_ids": reachable,
        "revision_ids": missing,
        "files": sorted(file_rows, key=lambda x: x["path"]),
        "object_count": len(seen_objects),
        "logical_file_count": len(file_rows),
        "skipped_nonportable_attestations": skipped_nonportable,
    }
    pack_id = "pack-" + _sha(_canon(payload))[:24]
    manifest = {**payload, "pack_id": pack_id}
    repo._write(out_dir / "pack.json", _json(manifest))
    return manifest


def _validate_logical_path(model_key: str, raw: str) -> PurePosixPath:
    p = PurePosixPath(raw)
    if p.is_absolute() or ".." in p.parts:
        raise RemoteError(f"unsafe pack path {raw!r}")
    model_dir = repo._directory_key(model_key)
    allowed_revision_prefix = ("models", model_dir, "revisions")
    allowed_attestation_prefix = ("attestations", model_dir)
    if p.parts[:3] == allowed_revision_prefix and len(p.parts) >= 5:
        return p
    if p.parts[:2] == allowed_attestation_prefix and len(p.parts) >= 4:
        return p
    if len(p.parts) == 2 and p.parts[0] == "keys" and p.name.endswith(".pub.pem"):
        return p
    raise RemoteError(f"pack path is outside transferable repository objects: {raw!r}")


def _verify_pack(pack_dir: Path) -> dict[str, Any]:
    manifest = repo._read_json(Path(pack_dir) / "pack.json")
    if manifest.get("format") != PACK_FORMAT:
        raise RemoteError("unsupported transfer pack format")
    payload = dict(manifest)
    pack_id = payload.pop("pack_id", None)
    expected = "pack-" + _sha(_canon(payload))[:24]
    if pack_id != expected:
        raise RemoteError("transfer pack manifest identity mismatch")
    for row in manifest.get("files", []):
        digest = row["sha256"]
        obj = Path(pack_dir) / "objects" / "sha256" / digest[:2] / digest
        if not obj.exists():
            raise RemoteError(f"missing content-addressed object {digest}")
        data = obj.read_bytes()
        if len(data) != int(row["bytes"]) or _sha(data) != digest:
            raise RemoteError(f"content-addressed object verification failed for {digest}")
        _validate_logical_path(manifest["model_key"], row["path"])
    return manifest


def _register_imported_revisions(root: Path, model_key: str, revision_ids: list[str]) -> None:
    root = Path(root)
    if not revision_ids:
        return
    model_dir = root / "models" / repo._directory_key(model_key)
    metas = [repo._revision_meta(root, model_key, rid) for rid in revision_ids]
    for meta in metas:
        if meta.get("model_key") != model_key:
            raise RemoteError(f"revision {meta.get('revision_id')} belongs to a different model key")
        version = str(meta.get("metamodel_version"))
        if not (root / "metamodels" / f"v{version}" / "metamodel.fg").exists():
            raise RemoteError(f"destination does not contain metamodel version {version}")
        for parent in meta.get("parent_revision_ids", []):
            if not (repo._revision_dir(root, model_key, parent) / "revision.json").exists():
                raise RemoteError(f"imported revision {meta['revision_id']} has unavailable parent {parent}")

    try:
        idx = repo._model_index(root, model_key)
    except repo.RepositoryError:
        idx = {
            "format": repo.MODEL_INDEX_FORMAT,
            "model_key": model_key,
            "directory": model_dir.name,
            "head_revision_id": None,
            "revisions": [],
        }
    revisions = list(idx.get("revisions", []))
    for rid in revision_ids:
        if rid not in revisions:
            revisions.append(rid)
    idx["revisions"] = revisions
    repo._write(model_dir / "model.json", _json(idx))
    if not repo._refs_path(root, model_key).exists():
        repo._write_refs(root, model_key, {
            "format": repo.REFS_FORMAT,
            "default_branch": repo.DEFAULT_BRANCH,
            "branches": {},
            "tags": {},
            "remote_tracking": {},
        })

    global_index = repo._load_models_index(root)
    entries = [x for x in global_index.get("models", []) if x["model_key"] != model_key]
    representative = metas[-1]
    head = idx.get("head_revision_id")
    if head:
        representative = repo._revision_meta(root, model_key, head)
    entries.append({
        "model_key": model_key,
        "directory": model_dir.name,
        "head_revision_id": head,
        "revision_count": len(revisions),
        "model_id": representative["model_id"],
        "model_name": representative["model_name"],
    })
    repo._write_models_index(root, {"format": repo.INDEX_FORMAT, "models": entries})


def apply_pack(destination_root: Path, pack_dir: Path) -> dict[str, Any]:
    destination_root = Path(destination_root)
    config = repo._repo_config(destination_root)
    manifest = _verify_pack(pack_dir)
    if manifest.get("destination_repository_id") != config["repository_id"]:
        raise RemoteError("transfer pack was built for a different destination repository")

    installed = skipped = 0
    for row in manifest.get("files", []):
        rel = _validate_logical_path(manifest["model_key"], row["path"])
        digest = row["sha256"]
        obj = Path(pack_dir) / "objects" / "sha256" / digest[:2] / digest
        dst = destination_root.joinpath(*rel.parts)
        if dst.exists():
            if _sha(dst.read_bytes()) != digest:
                raise RemoteError(f"immutable destination path differs: {rel.as_posix()}")
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(obj, dst)
        installed += 1
    _register_imported_revisions(destination_root, manifest["model_key"], list(manifest.get("revision_ids", [])))
    return {
        "format": "factgraph-transfer-pack-apply-v1",
        "pack_id": manifest["pack_id"],
        "model_key": manifest["model_key"],
        "revision_ids": manifest.get("revision_ids", []),
        "installed_file_count": installed,
        "already_present_file_count": skipped,
        "passed": True,
    }


def _is_ancestor(root: Path, model_key: str, ancestor: str, descendant: str) -> bool:
    if ancestor == descendant:
        return True
    return ancestor in repo._ancestor_distances(Path(root), model_key, descendant)


def _refresh_global_entry(root: Path, model_key: str) -> None:
    idx = repo._model_index(root, model_key)
    global_index = repo._load_models_index(root)
    entries = [x for x in global_index.get("models", []) if x["model_key"] != model_key]
    head = idx.get("head_revision_id")
    representative = repo._revision_meta(root, model_key, head) if head else repo._revision_meta(root, model_key, idx["revisions"][-1])
    entries.append({
        "model_key": model_key,
        "directory": repo._model_dir(root, model_key).name,
        "head_revision_id": head,
        "revision_count": len(idx.get("revisions", [])),
        "model_id": representative["model_id"],
        "model_name": representative["model_name"],
    })
    repo._write_models_index(root, {"format": repo.INDEX_FORMAT, "models": entries})


def update_branch_fast_forward(root: Path, model_key: str, branch: str, revision_id: str) -> dict[str, Any]:
    root = Path(root)
    branch = repo._validate_ref_name(branch)
    refs = repo._load_refs(root, model_key)
    old = refs.get("branches", {}).get(branch)
    if old and not _is_ancestor(root, model_key, old, revision_id):
        raise RemoteError(f"refusing non-fast-forward update of {model_key}/{branch}: {old} -> {revision_id}")
    trust = repo.evaluate_revision_trust(root, model_key, revision_id, branch=branch)
    if not trust["passed"]:
        raise RemoteError(
            f"protected branch trust policy rejected {revision_id}: "
            f"{trust['accepted_signature_count']}/{trust['required_valid_signatures']} accepted signatures"
        )
    refs.setdefault("branches", {})[branch] = revision_id
    repo._write_refs(root, model_key, refs)
    idx = repo._model_index(root, model_key)
    if branch == refs.get("default_branch", repo.DEFAULT_BRANCH):
        idx["head_revision_id"] = revision_id
        repo._write(repo._model_dir(root, model_key) / "model.json", _json(idx))
    _refresh_global_entry(root, model_key)
    return {
        "model_key": model_key,
        "branch": branch,
        "old_revision_id": old,
        "new_revision_id": revision_id,
        "fast_forward": True,
        "trust": trust,
    }


def _update_remote_tracking(root: Path, model_key: str, remote_name: str, branch: str, revision_id: str) -> None:
    refs = repo._load_refs(Path(root), model_key)
    refs.setdefault("remote_tracking", {}).setdefault(remote_name, {})[branch] = revision_id
    repo._write_refs(Path(root), model_key, refs)


def fetch(
    root: Path,
    remote_name: str,
    model_key: str,
    *,
    branch: str = repo.DEFAULT_BRANCH,
    out_dir: Path,
) -> dict[str, Any]:
    """Fetch a remote branch into immutable objects + a remote-tracking ref."""

    root = Path(root)
    remote_root = resolve_remote(root, remote_name)
    branch = repo._validate_ref_name(branch)
    remote_head = repo.resolve_revision(remote_root, model_key, branch)
    out_dir = Path(out_dir)
    pack_dir = out_dir / "pack"
    manifest = build_pack(remote_root, root, model_key, ref=branch, out_dir=pack_dir)
    applied = apply_pack(root, pack_dir)
    _update_remote_tracking(root, model_key, remote_name, branch, remote_head)
    remote_trust = repo.evaluate_revision_trust(remote_root, model_key, remote_head, branch=branch)
    report = {
        "format": TRANSFER_FORMAT,
        "direction": "fetch",
        "remote": remote_name,
        "model_key": model_key,
        "branch": branch,
        "remote_revision_id": remote_head,
        "local_branch_advanced": False,
        "remote_tracking_ref": f"{remote_name}/{branch}",
        "pack": manifest,
        "apply": applied,
        "remote_trust": remote_trust,
        "status": "fetched",
    }
    repo._write(out_dir / "transfer.json", _json(report))
    return report


def push(
    root: Path,
    remote_name: str,
    model_key: str,
    *,
    branch: str = repo.DEFAULT_BRANCH,
    out_dir: Path,
) -> dict[str, Any]:
    """Push one branch through a verified pack and fast-forward the remote ref."""

    root = Path(root)
    remote_root = resolve_remote(root, remote_name)
    branch = repo._validate_ref_name(branch)
    local_head = repo.resolve_revision(root, model_key, branch)
    out_dir = Path(out_dir)
    pack_dir = out_dir / "pack"
    manifest = build_pack(root, remote_root, model_key, ref=branch, out_dir=pack_dir)
    applied = apply_pack(remote_root, pack_dir)
    ref_update = None
    status = "objects_transferred"
    error = None
    try:
        ref_update = update_branch_fast_forward(remote_root, model_key, branch, local_head)
        status = "pushed"
        _update_remote_tracking(root, model_key, remote_name, branch, local_head)
    except Exception as exc:
        error = str(exc)
    report = {
        "format": TRANSFER_FORMAT,
        "direction": "push",
        "remote": remote_name,
        "model_key": model_key,
        "branch": branch,
        "local_revision_id": local_head,
        "pack": manifest,
        "apply": applied,
        "ref_update": ref_update,
        "status": status,
        "error": error,
    }
    repo._write(out_dir / "transfer.json", _json(report))
    if error:
        raise RemoteError(error)
    return report
