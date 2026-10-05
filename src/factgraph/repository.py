from __future__ import annotations

"""Content-verified Factgraph model repository.

The repository is intentionally filesystem-native and deterministic.  It stores
immutable model revisions plus the metamodel version used to encode each
revision.  Mutable files (repository/model indexes) only point at immutable
revision directories.

A repository revision contains both canonical semantic artifacts and versioned
metamodel populations.  Verification replays the important invariants rather
than trusting index metadata.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import re
import base64
from typing import Any, Iterable

from .diff import MigrationHints, semantic_diff
from .ids import slug, stable_id
from .metamodel import (
    CURRENT_METAMODEL_VERSION,
    SUPPORTED_METAMODEL_VERSIONS,
    build_metamodel,
    decode_model,
    encode_model,
    manifest_hash,
    metamodel_source,
    metamodel_versions,
    migration_report as migrate_population,
    population_dict,
    population_from_json,
    population_json,
    semantic_hash,
)
from .model import Model
from .identity import identity_report
from .normalize import normalize_model
from .parser import parse_model
from .printer import print_model
from .reporting import Severity, diagnostics_json
from .validate import validate_model


REPOSITORY_FORMAT = "factgraph-repository-v2"
INDEX_FORMAT = "factgraph-repository-index-v1"
MODEL_INDEX_FORMAT = "factgraph-repository-model-v2"
REVISION_FORMAT = "factgraph-repository-revision-v2"
VERIFY_FORMAT = "factgraph-repository-verification-v2"
MIGRATION_FORMAT = "factgraph-repository-metamodel-migration-v1"
REFS_FORMAT = "factgraph-repository-refs-v1"
TRUST_FORMAT = "factgraph-repository-trust-policy-v1"
DEFAULT_BRANCH = "main"


class RepositoryError(ValueError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _directory_key(model_key: str) -> str:
    readable = slug(model_key)[:48] or "model"
    digest = hashlib.sha256(model_key.encode("utf-8")).hexdigest()[:12]
    return f"{readable}--{digest}"


def _revision_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _load_model_source(text: str) -> Model:
    return normalize_model(parse_model(text))


def _validation_errors(model: Model):
    return [d for d in validate_model(model) if d.severity == Severity.ERROR]


def _repo_config(root: Path) -> dict[str, Any]:
    path = root / "repository.json"
    if not path.exists():
        raise RepositoryError(f"{root} is not a Factgraph repository")
    data = _read_json(path)
    if data.get("format") not in {REPOSITORY_FORMAT, "factgraph-repository-v1"}:
        raise RepositoryError(f"unsupported repository format {data.get('format')!r}")
    return data


def _models_index_path(root: Path) -> Path:
    return root / "models" / "index.json"


def _load_models_index(root: Path) -> dict[str, Any]:
    path = _models_index_path(root)
    if not path.exists():
        return {"format": INDEX_FORMAT, "models": []}
    data = _read_json(path)
    if data.get("format") != INDEX_FORMAT:
        raise RepositoryError("invalid models/index.json format")
    return data


def _write_models_index(root: Path, index: dict[str, Any]) -> None:
    index = {"format": INDEX_FORMAT, "models": sorted(index.get("models", []), key=lambda x: x["model_key"])}
    _write(_models_index_path(root), _json(index))


def _find_model_entry(root: Path, model_key: str) -> dict[str, Any] | None:
    for item in _load_models_index(root).get("models", []):
        if item["model_key"] == model_key:
            return item
    return None


def _model_dir(root: Path, model_key: str) -> Path:
    entry = _find_model_entry(root, model_key)
    directory = entry["directory"] if entry else _directory_key(model_key)
    return root / "models" / directory


def _model_index(root: Path, model_key: str) -> dict[str, Any]:
    path = _model_dir(root, model_key) / "model.json"
    if not path.exists():
        raise RepositoryError(f"repository has no model key {model_key!r}")
    data = _read_json(path)
    if data.get("format") not in {MODEL_INDEX_FORMAT, "factgraph-repository-model-v1"}:
        raise RepositoryError(f"invalid model index for {model_key!r}")
    return data


def _revision_dir(root: Path, model_key: str, revision_id: str) -> Path:
    return _model_dir(root, model_key) / "revisions" / revision_id


def _revision_meta(root: Path, model_key: str, revision_id: str) -> dict[str, Any]:
    path = _revision_dir(root, model_key, revision_id) / "revision.json"
    if not path.exists():
        raise RepositoryError(f"unknown revision {revision_id!r} for {model_key!r}")
    data = _read_json(path)
    if data.get("format") not in {REVISION_FORMAT, "factgraph-repository-revision-v1"}:
        raise RepositoryError(f"invalid revision format for {revision_id!r}")
    if "parent_revision_ids" not in data:
        parent = data.get("parent_revision_id")
        data["parent_revision_ids"] = [parent] if parent else []
    if "provenance" not in data:
        data["provenance"] = {}
    return data


def _revision_signature_payload_v1(root: Path, model_key: str, revision_id: str) -> dict[str, Any]:
    """Legacy repository-bound payload retained for v0.8 attestation verification."""
    config = _repo_config(root)
    meta = _revision_meta(root, model_key, revision_id)
    return {
        "format": "factgraph-revision-signing-payload-v1",
        "repository_id": config["repository_id"],
        "model_key": model_key,
        "revision_id": revision_id,
        "model_id": meta.get("model_id"),
        "parent_revision_ids": list(meta.get("parent_revision_ids", [])),
        "metamodel_version": meta.get("metamodel_version"),
        "semantic_sha256": meta.get("semantic_sha256"),
        "manifest_sha256": meta.get("manifest_sha256"),
        "operation": meta.get("operation"),
        "operation_details": meta.get("operation_details", {}),
        "provenance": meta.get("provenance", {}),
        "artifact_sha256": dict(sorted(meta.get("artifact_sha256", {}).items())),
    }


def _revision_signature_payload(root: Path, model_key: str, revision_id: str) -> dict[str, Any]:
    """Portable v2 payload over immutable revision content/ancestry.

    Repository identity is deliberately excluded so an attestation can travel
    with the immutable revision through a content-addressed remote pack. Trust
    remains a destination-local policy decision.
    """
    meta = _revision_meta(root, model_key, revision_id)
    return {
        "format": "factgraph-revision-signing-payload-v2",
        "model_key": model_key,
        "revision_id": revision_id,
        "model_id": meta.get("model_id"),
        "parent_revision_ids": list(meta.get("parent_revision_ids", [])),
        "metamodel_version": meta.get("metamodel_version"),
        "semantic_sha256": meta.get("semantic_sha256"),
        "manifest_sha256": meta.get("manifest_sha256"),
        "operation": meta.get("operation"),
        "operation_details": meta.get("operation_details", {}),
        "provenance": meta.get("provenance", {}),
        "artifact_sha256": dict(sorted(meta.get("artifact_sha256", {}).items())),
    }


def _signature_payload_for_attestation(root: Path, model_key: str, revision_id: str, attestation_format: str) -> dict[str, Any]:
    if attestation_format == "factgraph-revision-attestation-v1":
        return _revision_signature_payload_v1(root, model_key, revision_id)
    if attestation_format == "factgraph-revision-attestation-v2":
        return _revision_signature_payload(root, model_key, revision_id)
    raise RepositoryError(f"unsupported revision attestation format {attestation_format!r}")


def _refs_path(root: Path, model_key: str) -> Path:
    return _model_dir(root, model_key) / "refs.json"


def _validate_ref_name(name: str) -> str:
    name = str(name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", name):
        raise RepositoryError(f"invalid ref name {name!r}")
    if ".." in name or name.endswith("/") or "//" in name:
        raise RepositoryError(f"invalid ref name {name!r}")
    return name


def _load_refs(root: Path, model_key: str) -> dict[str, Any]:
    path = _refs_path(root, model_key)
    if path.exists():
        data = _read_json(path)
        if data.get("format") != REFS_FORMAT:
            raise RepositoryError(f"invalid refs for {model_key!r}")
        return data
    # v0.7 repositories were linear. Expose their head as a synthetic main
    # branch until the next write materializes refs.json.
    idx = _model_index(root, model_key)
    head = idx.get("head_revision_id")
    return {
        "format": REFS_FORMAT,
        "default_branch": DEFAULT_BRANCH,
        "branches": {DEFAULT_BRANCH: head} if head else {},
        "tags": {},
        "remote_tracking": {},
    }


def _write_refs(root: Path, model_key: str, refs: dict[str, Any]) -> None:
    payload = {
        "format": REFS_FORMAT,
        "default_branch": refs.get("default_branch", DEFAULT_BRANCH),
        "branches": dict(sorted(refs.get("branches", {}).items())),
        "tags": dict(sorted(refs.get("tags", {}).items())),
        "remote_tracking": {
            remote: dict(sorted(branches.items()))
            for remote, branches in sorted(refs.get("remote_tracking", {}).items())
        },
    }
    _write(_refs_path(root, model_key), _json(payload))


def resolve_revision(root: Path, model_key: str, ref: str | None = None) -> str:
    root = Path(root)
    idx = _model_index(root, model_key)
    if ref is None:
        refs = _load_refs(root, model_key)
        branch = refs.get("default_branch", DEFAULT_BRANCH)
        rid = refs.get("branches", {}).get(branch) or idx.get("head_revision_id")
        if not rid:
            raise RepositoryError(f"model {model_key!r} has no revision head")
        return rid
    if (_revision_dir(root, model_key, ref) / "revision.json").exists():
        return ref
    refs = _load_refs(root, model_key)
    if ref in refs.get("branches", {}):
        return refs["branches"][ref]
    if ref in refs.get("tags", {}):
        return refs["tags"][ref]
    raise RepositoryError(f"unknown revision/branch/tag {ref!r} for {model_key!r}")


def init_repository(
    root: Path,
    *,
    name: str = "FactgraphRepository",
    default_metamodel_version: str = CURRENT_METAMODEL_VERSION,
) -> dict[str, Any]:
    """Create a deterministic filesystem repository and snapshot all codecs."""

    root = Path(root)
    if default_metamodel_version not in SUPPORTED_METAMODEL_VERSIONS:
        raise RepositoryError(f"unsupported default metamodel version {default_metamodel_version!r}")
    config_path = root / "repository.json"
    if config_path.exists():
        existing = _repo_config(root)
        if existing["name"] != name:
            raise RepositoryError(
                f"repository already exists as {existing['name']!r}, not requested {name!r}"
            )
        if existing["default_metamodel_version"] != default_metamodel_version:
            raise RepositoryError(
                f"repository already uses default metamodel v{existing['default_metamodel_version']}, "
                f"not requested v{default_metamodel_version}"
            )
        return existing
    if root.exists() and any(root.iterdir()):
        raise RepositoryError(f"refusing to initialize non-empty directory {root}")
    root.mkdir(parents=True, exist_ok=True)
    config = {
        "format": REPOSITORY_FORMAT,
        "name": name,
        "repository_id": stable_id("repository", name),
        "default_metamodel_version": default_metamodel_version,
        "supported_metamodel_versions": list(SUPPORTED_METAMODEL_VERSIONS),
        "default_branch": DEFAULT_BRANCH,
    }
    _write(config_path, _json(config))
    _write_models_index(root, {"format": INDEX_FORMAT, "models": []})

    version_index = {"format": "factgraph-repository-metamodel-index-v1", "versions": []}
    for item in metamodel_versions():
        version = item["version"]
        mm = build_metamodel(version)
        base = root / "metamodels" / f"v{version}"
        _write(base / "metamodel.fg", metamodel_source(version))
        _write(base / "semantic.json", mm.semantic_json(include_samples=False))
        _write(base / "manifest.json", mm.manifest_json())
        _write(base / "version.json", _json(item))
        version_index["versions"].append(item)
    _write(root / "metamodels" / "index.json", _json(version_index))
    return config


def _artifact_hashes(directory: Path, names: Iterable[str]) -> dict[str, str]:
    return {
        name: _sha256_bytes((directory / name).read_bytes())
        for name in sorted(names)
    }


def _prepare_revision_files(
    directory: Path,
    *,
    model: Model,
    source_text: str,
    metamodel_version: str,
) -> dict[str, str]:
    diagnostics = validate_model(model)
    if any(d.severity == Severity.ERROR for d in diagnostics):
        first = next(d for d in diagnostics if d.severity == Severity.ERROR)
        raise RepositoryError(f"cannot store invalid model: {first.code}: {first.message}")

    core = encode_model(model, include_envelope=False, metamodel_version=metamodel_version)
    envelope = encode_model(model, include_envelope=True, metamodel_version=metamodel_version)
    core_recovered = decode_model(core, include_envelope=False, metamodel_version=metamodel_version)
    envelope_recovered = decode_model(envelope, include_envelope=True, metamodel_version=metamodel_version)
    if not model.semantically_equal(core_recovered):
        raise RepositoryError("core metamodel population failed semantic recovery before commit")
    if model.manifest_dict() != envelope_recovered.manifest_dict():
        raise RepositoryError("envelope metamodel population failed manifest recovery before commit")

    _write(directory / "source.fg", source_text)
    _write(directory / "normalized.fg", print_model(model))
    _write(directory / "semantic.json", model.semantic_json(include_samples=False))
    _write(directory / "manifest.json", model.manifest_json())
    _write(directory / "validation.json", diagnostics_json(diagnostics))
    _write(directory / "population.core.json", population_json(core))
    _write(directory / "population.envelope.json", population_json(envelope))
    _write(directory / "identity_coverage.json", _json(identity_report(model)))

    names = [
        "source.fg",
        "normalized.fg",
        "semantic.json",
        "manifest.json",
        "validation.json",
        "population.core.json",
        "population.envelope.json",
        "identity_coverage.json",
    ]
    return _artifact_hashes(directory, names)


def commit_model(
    root: Path,
    source_path: Path,
    *,
    model_key: str | None = None,
    metamodel_version: str | None = None,
    branch: str = DEFAULT_BRANCH,
    author: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    source_path = Path(source_path)
    return commit_model_text(
        root,
        source_path.read_text(encoding="utf-8"),
        model_key=model_key,
        metamodel_version=metamodel_version,
        branch=branch,
        provenance={"author": author, "message": message},
        source_label=source_path.name,
        operation="commit",
    )


def commit_model_text(
    root: Path,
    source_text: str,
    *,
    model_key: str | None = None,
    metamodel_version: str | None = None,
    source_label: str | None = None,
    operation: str = "commit",
    parent_revision_id: str | None = None,
    operation_details: dict[str, Any] | None = None,
    branch: str = DEFAULT_BRANCH,
    parent_revision_ids: Iterable[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _repo_config(Path(root))
    root = Path(root)
    metamodel_version = str(metamodel_version or config["default_metamodel_version"])
    if metamodel_version not in SUPPORTED_METAMODEL_VERSIONS:
        raise RepositoryError(f"unsupported metamodel version {metamodel_version!r}")
    model = _load_model_source(source_text)
    errors = _validation_errors(model)
    if errors:
        raise RepositoryError(f"model validation failed: {errors[0].code}: {errors[0].message}")
    model_key = str(model_key or model.id)
    branch = _validate_ref_name(branch)
    provenance = {k: v for k, v in (provenance or {}).items() if v is not None}

    existing_entry = _find_model_entry(root, model_key)
    existing_index = _model_index(root, model_key) if existing_entry else None
    refs = _load_refs(root, model_key) if existing_entry else {
        "format": REFS_FORMAT,
        "default_branch": DEFAULT_BRANCH,
        "branches": {},
        "tags": {},
        "remote_tracking": {},
    }
    if not existing_entry and branch != DEFAULT_BRANCH:
        raise RepositoryError(f"first revision for a model must be committed to {DEFAULT_BRANCH!r}")
    if existing_entry and branch not in refs.get("branches", {}):
        raise RepositoryError(f"unknown branch {branch!r}; create it before committing")
    current_head = refs.get("branches", {}).get(branch)
    if parent_revision_ids is None:
        if parent_revision_id is not None:
            parent_ids = [parent_revision_id]
        else:
            parent_ids = [current_head] if current_head else []
    else:
        parent_ids = [str(x) for x in parent_revision_ids if x]
        if parent_revision_id is not None and (not parent_ids or parent_ids[0] != parent_revision_id):
            raise RepositoryError("parent_revision_id conflicts with parent_revision_ids")
    if current_head is not None and (not parent_ids or parent_ids[0] != current_head):
        raise RepositoryError(
            f"first commit parent {parent_ids[0] if parent_ids else None!r} is not current {branch!r} head {current_head!r}"
        )
    for pid in parent_ids:
        _revision_meta(root, model_key, pid)

    sem_hash = semantic_hash(model)
    man_hash = manifest_hash(model)
    if current_head:
        current_meta = _revision_meta(root, model_key, current_head)
        if (
            current_meta["manifest_sha256"] == man_hash
            and current_meta["metamodel_version"] == metamodel_version
            and operation == "commit"
            and len(parent_ids) <= 1
        ):
            return {**current_meta, "status": "already_current"}

    id_payload = {
        "model_key": model_key,
        "parent_revision_ids": parent_ids,
        "metamodel_version": metamodel_version,
        "manifest_sha256": man_hash,
        "operation": operation,
        "operation_details": operation_details or {},
        "branch": branch,
        "provenance": provenance,
    }
    revision_id = f"rev-{_revision_digest(id_payload)}"
    final_dir = _revision_dir(root, model_key, revision_id)
    if final_dir.exists():
        existing = _revision_meta(root, model_key, revision_id)
        return {**existing, "status": "already_exists"}

    model_dir = _model_dir(root, model_key)
    model_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="factgraph-revision-", dir=str(model_dir)) as tmp_name:
        tmp = Path(tmp_name)
        hashes = _prepare_revision_files(
            tmp,
            model=model,
            source_text=source_text,
            metamodel_version=metamodel_version,
        )
        revision = {
            "format": REVISION_FORMAT,
            "revision_id": revision_id,
            "model_key": model_key,
            "model_id": model.id,
            "model_name": model.name,
            "parent_revision_id": parent_ids[0] if parent_ids else None,
            "parent_revision_ids": parent_ids,
            "metamodel_version": metamodel_version,
            "semantic_sha256": sem_hash,
            "manifest_sha256": man_hash,
            "source_label": source_label,
            "branch": branch,
            "provenance": provenance,
            "operation": operation,
            "operation_details": operation_details or {},
            "artifact_sha256": hashes,
        }
        _write(tmp / "revision.json", _json(revision))
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp.rename(final_dir)

    if existing_index is None:
        model_index = {
            "format": MODEL_INDEX_FORMAT,
            "model_key": model_key,
            "directory": model_dir.name,
            "head_revision_id": revision_id if branch == DEFAULT_BRANCH else None,
            "revisions": [revision_id],
        }
    else:
        revisions = list(existing_index.get("revisions", []))
        if revision_id not in revisions:
            revisions.append(revision_id)
        model_index = {
            **existing_index,
            "head_revision_id": revision_id if branch == DEFAULT_BRANCH else existing_index.get("head_revision_id"),
            "revisions": revisions,
        }
    _write(model_dir / "model.json", _json(model_index))
    refs.setdefault("branches", {})[branch] = revision_id
    refs.setdefault("tags", {})
    refs.setdefault("remote_tracking", {})
    refs.setdefault("default_branch", DEFAULT_BRANCH)
    _write_refs(root, model_key, refs)
    if branch == DEFAULT_BRANCH:
        model_index["head_revision_id"] = revision_id
        _write(model_dir / "model.json", _json(model_index))

    global_index = _load_models_index(root)
    entries = [x for x in global_index.get("models", []) if x["model_key"] != model_key]
    default_head = model_index.get("head_revision_id")
    head_meta = _revision_meta(root, model_key, default_head) if default_head else _revision_meta(root, model_key, revision_id)
    entries.append(
        {
            "model_key": model_key,
            "directory": model_dir.name,
            "head_revision_id": default_head,
            "revision_count": len(model_index["revisions"]),
            "model_id": head_meta["model_id"],
            "model_name": head_meta["model_name"],
        }
    )
    _write_models_index(root, {"format": INDEX_FORMAT, "models": entries})
    return {**_revision_meta(root, model_key, revision_id), "status": "committed"}


def list_models(root: Path) -> dict[str, Any]:
    _repo_config(Path(root))
    return _load_models_index(Path(root))


def log_model(root: Path, model_key: str) -> dict[str, Any]:
    root = Path(root)
    idx = _model_index(root, model_key)
    revisions = [_revision_meta(root, model_key, rid) for rid in idx.get("revisions", [])]
    return {
        "format": "factgraph-repository-log-v1",
        "model_key": model_key,
        "head_revision_id": idx.get("head_revision_id"),
        "refs": _load_refs(root, model_key),
        "revisions": revisions,
    }


def list_refs(root: Path, model_key: str) -> dict[str, Any]:
    _repo_config(Path(root))
    refs = _load_refs(Path(root), model_key)
    return {
        "format": REFS_FORMAT,
        "model_key": model_key,
        "default_branch": refs.get("default_branch", DEFAULT_BRANCH),
        "branches": dict(sorted(refs.get("branches", {}).items())),
        "tags": dict(sorted(refs.get("tags", {}).items())),
        "remote_tracking": {
            remote: dict(sorted(branches.items()))
            for remote, branches in sorted(refs.get("remote_tracking", {}).items())
        },
    }


def create_branch(
    root: Path,
    model_key: str,
    name: str,
    *,
    from_ref: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    _repo_config(root)
    name = _validate_ref_name(name)
    refs = _load_refs(root, model_key)
    if name in refs.get("branches", {}):
        raise RepositoryError(f"branch {name!r} already exists")
    if name in refs.get("tags", {}):
        raise RepositoryError(f"branch name {name!r} collides with a tag")
    rid = resolve_revision(root, model_key, from_ref)
    refs.setdefault("branches", {})[name] = rid
    _write_refs(root, model_key, refs)
    return {"model_key": model_key, "branch": name, "revision_id": rid, "status": "created"}


def delete_branch(root: Path, model_key: str, name: str) -> dict[str, Any]:
    root = Path(root)
    name = _validate_ref_name(name)
    refs = _load_refs(root, model_key)
    if name == refs.get("default_branch", DEFAULT_BRANCH):
        raise RepositoryError("cannot delete the default branch")
    if name not in refs.get("branches", {}):
        raise RepositoryError(f"unknown branch {name!r}")
    rid = refs["branches"].pop(name)
    _write_refs(root, model_key, refs)
    return {"model_key": model_key, "branch": name, "revision_id": rid, "status": "deleted"}


def create_tag(
    root: Path,
    model_key: str,
    name: str,
    *,
    ref: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    name = _validate_ref_name(name)
    refs = _load_refs(root, model_key)
    if name in refs.get("tags", {}):
        raise RepositoryError(f"tag {name!r} already exists; tags are immutable")
    if name in refs.get("branches", {}):
        raise RepositoryError(f"tag name {name!r} collides with a branch")
    rid = resolve_revision(root, model_key, ref)
    refs.setdefault("tags", {})[name] = rid
    _write_refs(root, model_key, refs)
    return {"model_key": model_key, "tag": name, "revision_id": rid, "status": "created"}


def _trust_path(root: Path) -> Path:
    return Path(root) / "trust-policy.json"


def _default_trust_policy() -> dict[str, Any]:
    return {
        "format": TRUST_FORMAT,
        "keys": {},
        "protected_branches": {},
    }


def load_trust_policy(root: Path) -> dict[str, Any]:
    root = Path(root)
    _repo_config(root)
    path = _trust_path(root)
    if not path.exists():
        return _default_trust_policy()
    data = _read_json(path)
    if data.get("format") != TRUST_FORMAT:
        raise RepositoryError("invalid trust-policy.json format")
    data.setdefault("keys", {})
    data.setdefault("protected_branches", {})
    return data


def _write_trust_policy(root: Path, policy: dict[str, Any]) -> None:
    payload = {
        "format": TRUST_FORMAT,
        "keys": dict(sorted(policy.get("keys", {}).items())),
        "protected_branches": {
            model_key: {
                branch: {
                    "min_valid_signatures": int(rule.get("min_valid_signatures", 1)),
                    "allowed_key_ids": sorted(set(rule.get("allowed_key_ids", []))),
                }
                for branch, rule in sorted(branches.items())
            }
            for model_key, branches in sorted(policy.get("protected_branches", {}).items())
        },
    }
    _write(_trust_path(Path(root)), _json(payload))


def trust_public_key(
    root: Path,
    public_key_path: Path,
    *,
    label: str | None = None,
) -> dict[str, Any]:
    """Register a public key and explicitly mark it trusted.

    Cryptographic validity and repository trust are intentionally separate. A
    key file arriving in a transfer pack is *not* trusted until this function
    (or an equivalent policy edit) records that decision.
    """

    from . import signing

    root = Path(root)
    _repo_config(root)
    public = signing.load_public_key(public_key_path)
    key_id = signing.key_id_from_public_key(public)
    serialization, _, _ = signing._crypto()
    pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_path = root / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
    if key_path.exists() and key_path.read_bytes() != pem:
        raise RepositoryError(f"registered key {key_id!r} has different bytes")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(pem)
    policy = load_trust_policy(root)
    previous = policy["keys"].get(key_id, {})
    policy["keys"][key_id] = {
        "status": "trusted",
        "label": label if label is not None else previous.get("label"),
        "reason": None,
    }
    _write_trust_policy(root, policy)
    return {"format": TRUST_FORMAT, "key_id": key_id, **policy["keys"][key_id]}


def revoke_trusted_key(root: Path, key_id: str, *, reason: str | None = None) -> dict[str, Any]:
    root = Path(root)
    policy = load_trust_policy(root)
    if key_id not in policy["keys"]:
        raise RepositoryError(f"unknown trusted key {key_id!r}")
    current = policy["keys"][key_id]
    policy["keys"][key_id] = {
        "status": "revoked",
        "label": current.get("label"),
        "reason": reason,
    }
    _write_trust_policy(root, policy)
    return {"format": TRUST_FORMAT, "key_id": key_id, **policy["keys"][key_id]}


def protect_branch(
    root: Path,
    model_key: str,
    branch: str,
    *,
    min_valid_signatures: int = 1,
    allowed_key_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    root = Path(root)
    _repo_config(root)
    branch = _validate_ref_name(branch)
    if min_valid_signatures < 1:
        raise RepositoryError("protected branch must require at least one valid signature")
    policy = load_trust_policy(root)
    allowed = sorted(set(str(x) for x in (allowed_key_ids or [])))
    for key_id in allowed:
        if key_id not in policy["keys"]:
            raise RepositoryError(f"protected branch references unknown trust key {key_id!r}")
    policy.setdefault("protected_branches", {}).setdefault(model_key, {})[branch] = {
        "min_valid_signatures": int(min_valid_signatures),
        "allowed_key_ids": allowed,
    }
    _write_trust_policy(root, policy)
    return {
        "format": TRUST_FORMAT,
        "model_key": model_key,
        "branch": branch,
        **policy["protected_branches"][model_key][branch],
    }


def unprotect_branch(root: Path, model_key: str, branch: str) -> dict[str, Any]:
    root = Path(root)
    branch = _validate_ref_name(branch)
    policy = load_trust_policy(root)
    branches = policy.get("protected_branches", {}).get(model_key, {})
    existed = branch in branches
    branches.pop(branch, None)
    if not branches:
        policy.get("protected_branches", {}).pop(model_key, None)
    _write_trust_policy(root, policy)
    return {"format": TRUST_FORMAT, "model_key": model_key, "branch": branch, "status": "removed" if existed else "absent"}


def _valid_revision_attestations(root: Path, model_key: str, revision_id: str) -> list[dict[str, Any]]:
    from . import signing

    root = Path(root)
    rows: list[dict[str, Any]] = []
    base = _attestation_dir(root, model_key, revision_id)
    if not base.exists():
        return rows
    for path in sorted(base.glob("*.json")):
        row: dict[str, Any] = {"path": str(path.relative_to(root)), "valid": False}
        try:
            a = _read_json(path)
            key_id = a["key_id"]
            row.update({"key_id": key_id, "signer": a.get("signer"), "attestation_format": a.get("format")})
            payload = _signature_payload_for_attestation(root, model_key, revision_id, a.get("format", "factgraph-revision-attestation-v1"))
            payload_sha = hashlib.sha256(signing.canonical_json_bytes(payload)).hexdigest()
            if a.get("payload_sha256") != payload_sha:
                raise RepositoryError("attestation payload hash mismatch")
            key_path = root / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
            if not key_path.exists():
                raise RepositoryError("registered public key missing")
            signing.verify_payload(payload, a["signature"], key_path)
            row["valid"] = True
            row["error"] = None
        except Exception as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def evaluate_revision_trust(
    root: Path,
    model_key: str,
    revision_id: str | None = None,
    *,
    branch: str | None = None,
) -> dict[str, Any]:
    """Evaluate cryptographically valid attestations against local trust policy."""

    root = Path(root)
    rid = resolve_revision(root, model_key, revision_id)
    policy = load_trust_policy(root)
    rule = None
    if branch is not None:
        rule = policy.get("protected_branches", {}).get(model_key, {}).get(branch)
    attestations = _valid_revision_attestations(root, model_key, rid)
    trusted_keys = {
        key_id
        for key_id, meta in policy.get("keys", {}).items()
        if meta.get("status") == "trusted"
    }
    allowed = set(rule.get("allowed_key_ids", [])) if rule else set()
    accepted = []
    rejected = []
    for row in attestations:
        key_id = row.get("key_id")
        reason = None
        if not row.get("valid"):
            reason = row.get("error") or "invalid signature"
        elif key_id not in trusted_keys:
            reason = "key is not trusted by this repository"
        elif allowed and key_id not in allowed:
            reason = "trusted key is not allowed for this protected branch"
        if reason is None:
            accepted.append(row)
        else:
            rejected.append({**row, "trust_error": reason})
    required = int(rule.get("min_valid_signatures", 0)) if rule else 0
    return {
        "format": "factgraph-repository-trust-evaluation-v1",
        "model_key": model_key,
        "revision_id": rid,
        "branch": branch,
        "protected": rule is not None,
        "required_valid_signatures": required,
        "accepted_signature_count": len(accepted),
        "passed": len(accepted) >= required,
        "accepted": accepted,
        "rejected": rejected,
    }


def _attestation_dir(root: Path, model_key: str, revision_id: str) -> Path:
    return root / "attestations" / _model_dir(root, model_key).name / revision_id


def sign_revision(
    root: Path,
    model_key: str,
    *,
    ref: str | None,
    private_key_path: Path,
    signer: str | None = None,
) -> dict[str, Any]:
    """Append an Ed25519 attestation for an immutable revision."""

    from . import signing

    root = Path(root)
    revision_id = resolve_revision(root, model_key, ref)
    payload = _revision_signature_payload(root, model_key, revision_id)
    private = signing.load_private_key(private_key_path)
    public = private.public_key()
    key_id = signing.key_id_from_public_key(public)
    serialization, _, _ = signing._crypto()
    signature = private.sign(signing.canonical_json_bytes(payload))
    public_pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_path = root / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
    if key_path.exists() and key_path.read_bytes() != public_pem:
        raise RepositoryError(f"registered key {key_id!r} has different bytes")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(public_pem)
    payload_sha = hashlib.sha256(signing.canonical_json_bytes(payload)).hexdigest()
    attestation = {
        "format": "factgraph-revision-attestation-v2",
        "algorithm": "ed25519",
        "key_id": key_id,
        "signer": signer,
        "model_key": model_key,
        "revision_id": revision_id,
        "payload_sha256": payload_sha,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    path = _attestation_dir(root, model_key, revision_id) / f"{key_id.replace(':', '--')}.json"
    if path.exists():
        existing = _read_json(path)
        if existing != attestation:
            raise RepositoryError(
                f"attestation for {revision_id} by {key_id} already exists with different metadata; attestations are append-only"
            )
        return {**existing, "attestation_path": str(path.relative_to(root)), "status": "already_exists"}
    _write(path, _json(attestation))
    return {**attestation, "attestation_path": str(path.relative_to(root)), "status": "signed"}


def verify_signatures(root: Path, *, model_key: str | None = None) -> dict[str, Any]:
    """Verify all repository attestations against registered public keys."""

    from . import signing

    root = Path(root)
    _repo_config(root)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    base = root / "attestations"
    if base.exists():
        for path in sorted(base.rglob("*.json")):
            try:
                a = _read_json(path)
                key_id = a["key_id"]
                mk = a["model_key"]
                if model_key is not None and mk != model_key:
                    continue
                rid = a["revision_id"]
                payload = _signature_payload_for_attestation(root, mk, rid, a.get("format", "factgraph-revision-attestation-v1"))
                payload_sha = hashlib.sha256(signing.canonical_json_bytes(payload)).hexdigest()
                if payload_sha != a.get("payload_sha256"):
                    raise RepositoryError("attestation payload hash mismatch")
                key_path = root / "keys" / f"{key_id.replace(':', '--')}.pub.pem"
                if not key_path.exists():
                    raise RepositoryError(f"registered public key missing for {key_id}")
                signing.verify_payload(payload, a["signature"], key_path)
                results.append({"path": str(path.relative_to(root)), "model_key": mk, "revision_id": rid, "key_id": key_id, "passed": True, "error": None})
            except Exception as exc:
                rel = str(path.relative_to(root))
                errors.append(f"{rel}: {exc}")
                results.append({"path": rel, "passed": False, "error": str(exc)})
    return {
        "format": "factgraph-repository-signature-verification-v1",
        "passed": not errors,
        "errors": errors,
        "attestation_count": len(results),
        "results": results,
    }


def load_revision_model(root: Path, model_key: str, revision_id: str | None = None) -> Model:
    root = Path(root)
    idx = _model_index(root, model_key)
    revision_id = resolve_revision(root, model_key, revision_id)
    manifest = _revision_dir(root, model_key, revision_id) / "manifest.json"
    return Model.from_manifest_json(manifest.read_text(encoding="utf-8"))


def checkout_revision(
    root: Path,
    model_key: str,
    out_dir: Path,
    *,
    revision_id: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    idx = _model_index(root, model_key)
    revision_id = resolve_revision(root, model_key, revision_id)
    src = _revision_dir(root, model_key, revision_id)
    if not src.exists():
        raise RepositoryError(f"unknown revision {revision_id!r}")
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(src, out_dir)
    return _revision_meta(root, model_key, revision_id)


def diff_revisions(
    root: Path,
    model_key: str,
    before_revision: str,
    after_revision: str,
    *,
    hints: MigrationHints | None = None,
):
    before = load_revision_model(root, model_key, before_revision)
    after = load_revision_model(root, model_key, after_revision)
    return semantic_diff(before, after, hints or MigrationHints.empty())


def _ancestor_distances(root: Path, model_key: str, revision_id: str) -> dict[str, int]:
    distances: dict[str, int] = {revision_id: 0}
    queue = [revision_id]
    while queue:
        rid = queue.pop(0)
        distance = distances[rid]
        for parent in _revision_meta(root, model_key, rid).get("parent_revision_ids", []):
            if parent not in distances or distance + 1 < distances[parent]:
                distances[parent] = distance + 1
                queue.append(parent)
    return distances


def find_merge_base(root: Path, model_key: str, ours_ref: str, theirs_ref: str) -> str:
    root = Path(root)
    ours = resolve_revision(root, model_key, ours_ref)
    theirs = resolve_revision(root, model_key, theirs_ref)
    od = _ancestor_distances(root, model_key, ours)
    td = _ancestor_distances(root, model_key, theirs)
    common = set(od) & set(td)
    if not common:
        raise RepositoryError("branches have no common ancestor")
    return min(common, key=lambda rid: (max(od[rid], td[rid]), od[rid] + td[rid], rid))


def merge_refs(
    root: Path,
    model_key: str,
    *,
    ours: str,
    theirs: str,
    resolutions: dict[str, str] | None = None,
    commit: bool = False,
    author: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Plan or commit a three-way semantic merge between repository refs."""

    from .merge import semantic_merge

    root = Path(root)
    refs = _load_refs(root, model_key)
    if commit and ours not in refs.get("branches", {}):
        raise RepositoryError("--commit requires 'ours' to name a branch, not a tag/revision")
    ours_id = resolve_revision(root, model_key, ours)
    theirs_id = resolve_revision(root, model_key, theirs)
    base_id = find_merge_base(root, model_key, ours, theirs)
    base_model = load_revision_model(root, model_key, base_id)
    ours_model = load_revision_model(root, model_key, ours_id)
    theirs_model = load_revision_model(root, model_key, theirs_id)
    merged = semantic_merge(base_model, ours_model, theirs_model, resolutions=resolutions)
    report: dict[str, Any] = {
        "format": "factgraph-repository-merge-v2",
        "model_key": model_key,
        "base_revision_id": base_id,
        "ours": {"ref": ours, "revision_id": ours_id},
        "theirs": {"ref": theirs, "revision_id": theirs_id},
        "merge": merged.to_dict(),
        "identity_coverage": {
            "base": identity_report(base_model),
            "ours": identity_report(ours_model),
            "theirs": identity_report(theirs_model),
        },
        "commit": None,
    }
    if commit:
        if merged.status != "merged" or merged.merged_model is None:
            raise RepositoryError("cannot commit merge while semantic conflicts/validation errors remain")
        ours_meta = _revision_meta(root, model_key, ours_id)
        committed = commit_model_text(
            root,
            print_model(merged.merged_model),
            model_key=model_key,
            metamodel_version=ours_meta["metamodel_version"],
            source_label=f"semantic merge {ours} <- {theirs}",
            operation="merge",
            operation_details={
                "base_revision_id": base_id,
                "ours_revision_id": ours_id,
                "theirs_revision_id": theirs_id,
                "resolutions": dict(sorted((resolutions or {}).items())),
            },
            branch=ours,
            parent_revision_ids=[ours_id, theirs_id],
            provenance={"author": author, "message": message},
        )
        report["commit"] = committed
    return report


def _verify_revision(root: Path, model_key: str, revision_id: str) -> dict[str, Any]:
    directory = _revision_dir(root, model_key, revision_id)
    meta = _revision_meta(root, model_key, revision_id)
    errors: list[str] = []
    warnings: list[str] = []

    for name, expected in sorted(meta.get("artifact_sha256", {}).items()):
        path = directory / name
        if not path.exists():
            errors.append(f"missing artifact {name}")
        elif _sha256_bytes(path.read_bytes()) != expected:
            errors.append(f"artifact hash mismatch: {name}")

    try:
        stored_model = Model.from_manifest_json((directory / "manifest.json").read_text(encoding="utf-8"))
        if semantic_hash(stored_model) != meta["semantic_sha256"]:
            errors.append("semantic hash mismatch")
        if manifest_hash(stored_model) != meta["manifest_sha256"]:
            errors.append("manifest hash mismatch")
        normalized = print_model(stored_model)
        if normalized != (directory / "normalized.fg").read_text(encoding="utf-8"):
            errors.append("normalized.fg does not match manifest model")
        parsed_source = _load_model_source((directory / "source.fg").read_text(encoding="utf-8"))
        if parsed_source.manifest_dict() != stored_model.manifest_dict():
            warnings.append(
                "source.fg parses to a different compiler manifest; this can occur for metamodel-migration commits that intentionally preserve source provenance only if operation metadata says so"
            )

        version = meta["metamodel_version"]
        core = population_from_json((directory / "population.core.json").read_text(encoding="utf-8"))
        envelope = population_from_json((directory / "population.envelope.json").read_text(encoding="utf-8"))
        core_subject = decode_model(core, include_envelope=False, metamodel_version=version)
        full_subject = decode_model(envelope, include_envelope=True, metamodel_version=version)
        if not stored_model.semantically_equal(core_subject):
            errors.append("core population does not recover stored semantic model")
        if stored_model.manifest_dict() != full_subject.manifest_dict():
            errors.append("envelope population does not recover stored manifest")
        if population_dict(encode_model(stored_model, include_envelope=False, metamodel_version=version)) != population_dict(core):
            errors.append("core population is not canonical for stored model")
        if population_dict(encode_model(stored_model, include_envelope=True, metamodel_version=version)) != population_dict(envelope):
            errors.append("envelope population is not canonical for stored model")
        stored_identity = _read_json(directory / "identity_coverage.json")
        if stored_identity != identity_report(stored_model):
            errors.append("identity coverage report does not match stored model")
    except Exception as exc:  # report all verification failures as data, not crashes
        errors.append(f"verification exception: {exc}")

    return {
        "model_key": model_key,
        "revision_id": revision_id,
        "metamodel_version": meta.get("metamodel_version"),
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def verify_repository(root: Path) -> dict[str, Any]:
    root = Path(root)
    config = _repo_config(root)
    errors: list[str] = []
    warnings: list[str] = []
    metamodel_checks: list[dict[str, Any]] = []

    try:
        mm_index = _read_json(root / "metamodels" / "index.json")
        for item in mm_index.get("versions", []):
            version = str(item["version"])
            base = root / "metamodels" / f"v{version}"
            source = (base / "metamodel.fg").read_text(encoding="utf-8")
            mm = _load_model_source(source)
            local_errors = []
            if semantic_hash(mm) != item["semantic_sha256"]:
                local_errors.append("snapshot semantic hash mismatch")
            if version in SUPPORTED_METAMODEL_VERSIONS and source != metamodel_source(version):
                local_errors.append("snapshot differs from packaged metamodel source")
            metamodel_checks.append({"version": version, "passed": not local_errors, "errors": local_errors})
            errors.extend(f"metamodel v{version}: {e}" for e in local_errors)
    except Exception as exc:
        errors.append(f"metamodel index verification failed: {exc}")

    model_results: list[dict[str, Any]] = []
    global_index = _load_models_index(root)
    for entry in global_index.get("models", []):
        model_key = entry["model_key"]
        try:
            idx = _model_index(root, model_key)
            revisions = idx.get("revisions", [])
            revision_set = set(revisions)
            if entry.get("revision_count") != len(revisions):
                errors.append(f"{model_key}: global revision_count mismatch")
            if entry.get("head_revision_id") != idx.get("head_revision_id"):
                errors.append(f"{model_key}: global/model head mismatch")
            refs = _load_refs(root, model_key)
            default_branch = refs.get("default_branch", DEFAULT_BRANCH)
            default_head = refs.get("branches", {}).get(default_branch)
            if idx.get("head_revision_id") != default_head:
                errors.append(f"{model_key}: model head does not match default branch {default_branch!r}")
            for ref_kind in ("branches", "tags"):
                for name, rid in refs.get(ref_kind, {}).items():
                    try:
                        _validate_ref_name(name)
                    except Exception as exc:
                        errors.append(f"{model_key}: invalid {ref_kind[:-2]} ref {name!r}: {exc}")
                    if rid not in revision_set:
                        errors.append(f"{model_key}: {ref_kind[:-2]} {name!r} points to unknown revision {rid!r}")
            for remote_name, tracking in refs.get("remote_tracking", {}).items():
                try:
                    _validate_ref_name(remote_name)
                except Exception as exc:
                    errors.append(f"{model_key}: invalid remote tracking name {remote_name!r}: {exc}")
                for branch_name, rid in tracking.items():
                    try:
                        _validate_ref_name(branch_name)
                    except Exception as exc:
                        errors.append(f"{model_key}: invalid remote branch {remote_name}/{branch_name}: {exc}")
                    if rid not in revision_set:
                        errors.append(f"{model_key}: remote tracking {remote_name}/{branch_name} points to unknown revision {rid!r}")

            parents: dict[str, list[str]] = {}
            for rid in revisions:
                meta = _revision_meta(root, model_key, rid)
                pids = list(meta.get("parent_revision_ids", []))
                parents[rid] = pids
                if meta.get("parent_revision_id") != (pids[0] if pids else None):
                    errors.append(f"{model_key}:{rid}: parent_revision_id compatibility field disagrees with parent_revision_ids")
                for pid in pids:
                    if pid not in revision_set:
                        errors.append(f"{model_key}:{rid}: unknown parent revision {pid!r}")
                result = _verify_revision(root, model_key, rid)
                model_results.append(result)
                errors.extend(f"{model_key}:{rid}: {e}" for e in result["errors"])
                warnings.extend(f"{model_key}:{rid}: {w}" for w in result["warnings"])

            visiting: set[str] = set()
            visited: set[str] = set()
            def visit(rid: str) -> None:
                if rid in visited:
                    return
                if rid in visiting:
                    errors.append(f"{model_key}: revision graph contains a cycle at {rid}")
                    return
                visiting.add(rid)
                for pid in parents.get(rid, []):
                    if pid in revision_set:
                        visit(pid)
                visiting.remove(rid)
                visited.add(rid)
            for rid in revisions:
                visit(rid)
        except Exception as exc:
            errors.append(f"{model_key}: index verification exception: {exc}")

    signature_report = verify_signatures(root)
    errors.extend(f"signature: {e}" for e in signature_report.get("errors", []))

    trust_policy = load_trust_policy(root)
    trust_checks: list[dict[str, Any]] = []
    for model_key, branches in sorted(trust_policy.get("protected_branches", {}).items()):
        try:
            refs = _load_refs(root, model_key)
        except Exception as exc:
            errors.append(f"trust policy {model_key}: {exc}")
            continue
        for branch, rule in sorted(branches.items()):
            rid = refs.get("branches", {}).get(branch)
            if not rid:
                errors.append(f"trust policy {model_key}/{branch}: protected branch does not exist")
                continue
            for key_id in rule.get("allowed_key_ids", []):
                if key_id not in trust_policy.get("keys", {}):
                    errors.append(f"trust policy {model_key}/{branch}: unknown allowed key {key_id}")
            check = evaluate_revision_trust(root, model_key, rid, branch=branch)
            trust_checks.append(check)
            if not check["passed"]:
                errors.append(
                    f"trust policy {model_key}/{branch}: requires {check['required_valid_signatures']} accepted signature(s), "
                    f"found {check['accepted_signature_count']}"
                )

    return {
        "format": VERIFY_FORMAT,
        "repository": {
            "name": config["name"],
            "repository_id": config["repository_id"],
            "default_metamodel_version": config["default_metamodel_version"],
        },
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "metamodel_checks": metamodel_checks,
        "revision_checks": model_results,
        "signature_verification": signature_report,
        "trust_policy": trust_policy,
        "trust_checks": trust_checks,
        "summary": {
            "model_count": len(global_index.get("models", [])),
            "revision_count": len(model_results),
            "revision_pass_count": sum(1 for x in model_results if x["passed"]),
            "metamodel_version_count": len(metamodel_checks),
            "attestation_count": signature_report.get("attestation_count", 0),
            "protected_branch_count": len(trust_checks),
            "protected_branch_pass_count": sum(1 for x in trust_checks if x.get("passed")),
        },
    }


def migrate_repository_metamodel(
    root: Path,
    *,
    target_version: str,
    model_key: str | None = None,
) -> dict[str, Any]:
    """Create immutable head revisions re-encoded under ``target_version``."""

    root = Path(root)
    _repo_config(root)
    if target_version not in SUPPORTED_METAMODEL_VERSIONS:
        raise RepositoryError(f"unsupported target metamodel version {target_version!r}")
    entries = _load_models_index(root).get("models", [])
    if model_key is not None:
        entries = [x for x in entries if x["model_key"] == model_key]
        if not entries:
            raise RepositoryError(f"repository has no model key {model_key!r}")

    results = []
    for entry in list(entries):
        key = entry["model_key"]
        idx = _model_index(root, key)
        head = idx["head_revision_id"]
        head_meta = _revision_meta(root, key, head)
        source_version = head_meta["metamodel_version"]
        if source_version == target_version:
            results.append(
                {
                    "model_key": key,
                    "source_revision_id": head,
                    "source_version": source_version,
                    "target_version": target_version,
                    "status": "already_current",
                }
            )
            continue
        head_dir = _revision_dir(root, key, head)
        populated = population_from_json(
            (head_dir / "population.envelope.json").read_text(encoding="utf-8")
        )
        migrated, pop_report = migrate_population(
            populated, target_version=target_version, include_envelope=True
        )
        if not pop_report["migration_passed"]:
            raise RepositoryError(
                f"metamodel migration failed equality contract for {key}: {source_version} -> {target_version}"
            )
        recovered = decode_model(
            migrated, include_envelope=True, metamodel_version=target_version
        )
        # Preserve the original source artifact when it still parses to the same
        # full compiler manifest. Otherwise use canonical normalized source.
        old_source = (head_dir / "source.fg").read_text(encoding="utf-8")
        try:
            same_source_manifest = _load_model_source(old_source).manifest_dict() == recovered.manifest_dict()
        except Exception:
            same_source_manifest = False
        source_text = old_source if same_source_manifest else print_model(recovered)
        commit = commit_model_text(
            root,
            source_text,
            model_key=key,
            metamodel_version=target_version,
            source_label=f"metamodel migration of {head}",
            operation="metamodel_migration",
            parent_revision_id=head,
            operation_details={
                "from_version": source_version,
                "to_version": target_version,
                "population_migration": pop_report,
            },
        )
        new_dir = _revision_dir(root, key, commit["revision_id"])
        _write(new_dir / "metamodel_migration.json", _json(pop_report))
        # Add the migration report to the immutable revision's own hash list.
        meta = _revision_meta(root, key, commit["revision_id"])
        meta["artifact_sha256"]["metamodel_migration.json"] = _sha256_bytes(
            (new_dir / "metamodel_migration.json").read_bytes()
        )
        _write(new_dir / "revision.json", _json(meta))
        results.append(
            {
                "model_key": key,
                "source_revision_id": head,
                "target_revision_id": commit["revision_id"],
                "source_version": source_version,
                "target_version": target_version,
                "status": "migrated",
                "semantic_roundtrip_equal": pop_report["semantic_roundtrip_equal"],
                "manifest_roundtrip_equal": pop_report["manifest_roundtrip_equal"],
            }
        )

    verification = verify_repository(root)
    return {
        "format": MIGRATION_FORMAT,
        "target_version": target_version,
        "results": results,
        "repository_verification_passed": verification["passed"],
        "repository_verification_errors": verification["errors"],
    }
