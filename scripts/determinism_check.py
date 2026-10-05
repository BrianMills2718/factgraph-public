#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from factgraph import __version__  # noqa: E402
from factgraph.cli import build, build_migration, write_metamodel_bundle  # noqa: E402
from factgraph import metamodel as mm  # noqa: E402
from factgraph import repository as repo  # noqa: E402
from factgraph import remote as remote_repo  # noqa: E402
from factgraph import audit as semantic_audit  # noqa: E402
from factgraph.targets import postgres as pg_target, mongo as mongo_target, typedb as typedb_target  # noqa: E402
from factgraph.mutations import write_mutation_catalog  # noqa: E402
from factgraph.importers.ossie import import_ossie  # noqa: E402
from factgraph.importers.linkml import import_linkml  # noqa: E402
from factgraph.importers.factum import import_factum  # noqa: E402
from factgraph.printer import print_model  # noqa: E402


def files(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def build_model(path: Path):
    from factgraph.parser import parse_model
    from factgraph.normalize import normalize_model
    return normalize_model(parse_model(path.read_text(encoding="utf-8")))


def build_tree(dst: Path) -> None:
    for src in sorted((ROOT / "examples").glob("*.fg")):
        build(src, dst / "examples" / src.stem)
    write_metamodel_bundle(dst / "metamodel_self")
    write_metamodel_bundle(dst / "metamodel_v1", "1")
    for d in sorted(p for p in (ROOT / "examples" / "migrations").iterdir() if p.is_dir()):
        hints = d / "hints.json"
        build_migration(d / "before.fg", d / "after.fg", dst / "migrations" / d.name, hints if hints.exists() else None)

    # v0.11 semantic portability evidence: the public 20-case corpus, target
    # projections, obligation-level audit bundles, mutation catalog, and three
    # external-format frontends. Live observations are intentionally excluded
    # from determinism because target/server responses are environment evidence.
    portability_models = {}
    portability_root = dst / "portability_v010"
    for src in sorted((ROOT / "examples" / "portability").glob("*.fg")):
        model = build_model(src)
        portability_models[src.stem] = model
        out = portability_root / "cases" / src.stem
        semantic_audit.write_audit_bundle(model, out / "audit", targets=("postgres", "mongo", "typedb"))
        (out / "normalized.fg").parent.mkdir(parents=True, exist_ok=True)
        (out / "normalized.fg").write_text(print_model(model), encoding="utf-8")
        (out / "postgres.sql").write_text(pg_target.emit_sql(model), encoding="utf-8")
        (out / "mongo.json").write_text(mongo_target.emit_spec_json(model), encoding="utf-8")
        (out / "typedb.tql").write_text(typedb_target.emit_schema(model), encoding="utf-8")
    write_mutation_catalog(portability_models, portability_root / "mutations")
    (portability_root / "expectations.json").write_bytes((ROOT / "examples" / "portability" / "expectations.json").read_bytes())

    external_root = dst / "external_v010"
    for name, source, importer in [
        ("ossie", ROOT / "examples" / "external" / "ossie_people.yaml", import_ossie),
        ("linkml", ROOT / "examples" / "external" / "linkml_people.yaml", import_linkml),
        ("factum", ROOT / "examples" / "external" / "factum_people.orm.json", import_factum),
    ]:
        result = importer(source.read_text(encoding="utf-8"))
        out = external_root / name
        out.mkdir(parents=True, exist_ok=True)
        (out / "source.txt").write_bytes(source.read_bytes())
        (out / "normalized.fg").write_text(result.generated_source, encoding="utf-8")
        (out / "import_report.json").write_text(__import__("json").dumps(result.report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        semantic_audit.write_audit_bundle(result.model, out / "audit", targets=("postgres", "mongo", "typedb"))

    # v0.7 metamodel evolution evidence.
    mdiff = mm.metamodel_semantic_diff("1", "2")
    md = dst / "metamodel_versions"
    md.mkdir(parents=True, exist_ok=True)
    (md / "versions.json").write_text(__import__("json").dumps({"current_version": mm.CURRENT_METAMODEL_VERSION, "versions": mm.metamodel_versions()}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (md / "semantic_diff.json").write_text(mdiff.to_json(), encoding="utf-8")
    warehouse = build_model(ROOT / "examples" / "warehouse.fg")
    old_pop = mm.encode_model(warehouse, include_envelope=True, metamodel_version="1")
    migrated, mreport = mm.migration_report(old_pop, target_version="2", include_envelope=True)
    (md / "source.population.json").write_text(mm.population_json(old_pop), encoding="utf-8")
    (md / "migrated.population.json").write_text(mm.population_json(migrated), encoding="utf-8")
    (md / "migration.json").write_text(__import__("json").dumps(mreport, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # v0.7 immutable repository history + metamodel migration.
    rroot = dst / "repository_demo" / "repository"
    repo.init_repository(rroot, name="FactgraphDemoRepository", default_metamodel_version="1")
    base = ROOT / "examples" / "migrations" / "safe_risky"
    first = repo.commit_model(rroot, base / "before.fg", model_key="team-app", metamodel_version="1")
    second = repo.commit_model(rroot, base / "after.fg", model_key="team-app", metamodel_version="1")
    ddiff = repo.diff_revisions(rroot, "team-app", first["revision_id"], second["revision_id"])
    out = dst / "repository_demo"
    (out / "domain_diff.json").write_text(ddiff.to_json(), encoding="utf-8")
    migration = repo.migrate_repository_metamodel(rroot, target_version="2", model_key="team-app")
    (out / "metamodel_migration.json").write_text(__import__("json").dumps(migration, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "verification.json").write_text(__import__("json").dumps(repo.verify_repository(rroot), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # v0.8 repository evolution evidence: rename-stable IDs, refs, semantic
    # merge/conflict reporting, and (when cryptography is installed) a
    # deterministic Ed25519 attestation.
    v08 = ROOT / "examples" / "repository_v08"
    rroot8 = dst / "repository_v08" / "repository"
    repo.init_repository(rroot8, name="FactgraphV08Determinism")
    base8 = repo.commit_model(rroot8, v08 / "base.fg", model_key="team-app-v08", author="Demo", message="base")
    repo.create_branch(rroot8, "team-app-v08", "feature/person", from_ref="main")
    repo.create_tag(rroot8, "team-app-v08", "stable-v1", ref="main")
    repo.commit_model(rroot8, v08 / "main.fg", model_key="team-app-v08", branch="main", author="Demo", message="main edit")
    repo.commit_model(rroot8, v08 / "feature_person.fg", model_key="team-app-v08", branch="feature/person", author="Demo", message="rename by identity")
    clean8 = repo.merge_refs(rroot8, "team-app-v08", ours="main", theirs="feature/person", commit=True, author="Merge Bot", message="clean semantic merge")
    repo.create_tag(rroot8, "team-app-v08", "merged-v1", ref="main")
    repo.create_branch(rroot8, "team-app-v08", "conflict/ours", from_ref="stable-v1")
    repo.create_branch(rroot8, "team-app-v08", "conflict/theirs", from_ref="stable-v1")
    repo.commit_model(rroot8, v08 / "conflict_ours.fg", model_key="team-app-v08", branch="conflict/ours", message="ours conflict")
    repo.commit_model(rroot8, v08 / "conflict_theirs.fg", model_key="team-app-v08", branch="conflict/theirs", message="theirs conflict")
    conflict8 = repo.merge_refs(rroot8, "team-app-v08", ours="conflict/ours", theirs="conflict/theirs")
    resolutions = {c["id"]: "ours" for c in conflict8["merge"]["conflicts"]}
    resolved8 = repo.merge_refs(rroot8, "team-app-v08", ours="conflict/ours", theirs="conflict/theirs", resolutions=resolutions)

    signed = False
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        private = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
        key_path = dst / ".v08-determinism-private.pem"
        key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        repo.sign_revision(rroot8, "team-app-v08", ref="main", private_key_path=key_path, signer="Factgraph deterministic audit key")
        key_path.unlink()
        signed = True
    except ImportError:
        pass

    out8 = dst / "repository_v08"
    summary8 = {
        "format": "factgraph-v0.8-determinism-repository-v1",
        "base_revision_id": base8["revision_id"],
        "clean_merge_status": clean8["merge"]["status"],
        "clean_merge_parent_count": len(clean8["commit"]["parent_revision_ids"]),
        "conflict_count": conflict8["merge"]["summary"]["conflict_count"],
        "resolved_merge_status": resolved8["merge"]["status"],
        "signed": signed,
        "refs": repo.list_refs(rroot8, "team-app-v08"),
        "verification": repo.verify_repository(rroot8),
    }
    (out8 / "summary.json").write_text(__import__("json").dumps(summary8, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # v0.9 semantic collaboration: content-addressed filesystem transfer,
    # portable attestation trust, fetch-only tracking, and explained conflicts.
    collab = dst / "repository_v09"
    author = collab / "author"
    hub = collab / "hub"
    reviewer = collab / "reviewer"
    repo.init_repository(author, name="CollaborationAuthor")
    repo.init_repository(hub, name="CollaborationHub")
    repo.init_repository(reviewer, name="CollaborationReviewer")
    repo.commit_model(author, v08 / "base.fg", model_key="team-app-v09", author="Ada", message="base")
    remote_repo.add_remote(author, "origin", hub)
    remote_repo.add_remote(reviewer, "origin", hub)
    signed_push = fetch_report = None
    unsigned_blocked = False
    if signed:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
        private_path = collab / ".private.pem"
        public_path = collab / ".public.pem"
        private_path.parent.mkdir(parents=True, exist_ok=True)
        private_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        public_path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        trusted = repo.trust_public_key(hub, public_path, label="deterministic collaboration key")
        repo.protect_branch(hub, "team-app-v09", "main", min_valid_signatures=1, allowed_key_ids=[trusted["key_id"]])
        try:
            remote_repo.push(author, "origin", "team-app-v09", out_dir=collab / "push_unsigned")
        except remote_repo.RemoteError:
            unsigned_blocked = True
        repo.sign_revision(author, "team-app-v09", ref="main", private_key_path=private_path, signer="deterministic collaboration")
        private_path.unlink()
        public_path.unlink()
        signed_push = remote_repo.push(author, "origin", "team-app-v09", out_dir=collab / "push_signed")
        remote_repo.push(author, "origin", "team-app-v09", out_dir=collab / "push_dedup")
        fetch_report = remote_repo.fetch(reviewer, "origin", "team-app-v09", out_dir=collab / "fetch")

    from factgraph.merge import semantic_merge
    explained = semantic_merge(
        build_model(v08 / "base.fg"),
        build_model(v08 / "conflict_ours.fg"),
        build_model(v08 / "conflict_theirs.fg"),
    )
    (collab / "merge_explanation.md").write_text(explained.to_markdown(), encoding="utf-8")
    summary9 = {
        "format": "factgraph-v0.9-determinism-collaboration-v1",
        "unsigned_push_blocked": unsigned_blocked,
        "signed_push_status": signed_push.get("status") if signed_push else "signing_unavailable",
        "fetch_status": fetch_report.get("status") if fetch_report else "signing_unavailable",
        "fetch_local_branch_advanced": fetch_report.get("local_branch_advanced") if fetch_report else None,
        "reviewer_refs": repo.list_refs(reviewer, "team-app-v09") if fetch_report else {},
        "hub_verification": repo.verify_repository(hub),
        "merge_conflict_count": len(explained.conflicts),
        "merge_explanation_sha256": hashlib.sha256(explained.to_markdown().encode("utf-8")).hexdigest(),
    }
    (collab / "summary.json").write_text(__import__("json").dumps(summary9, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="factgraph-det-a-") as a, tempfile.TemporaryDirectory(prefix="factgraph-det-b-") as b:
        pa, pb = Path(a), Path(b)
        build_tree(pa)
        build_tree(pb)
        fa, fb = files(pa), files(pb)
        keys = sorted(set(fa) | set(fb))
        mismatches = [k for k in keys if fa.get(k) != fb.get(k)]
        ordinary = len(list((ROOT / "examples").glob("*.fg")))
        migrations = len([p for p in (ROOT / "examples" / "migrations").iterdir() if p.is_dir()])
        portability = len(list((ROOT / "examples" / "portability").glob("*.fg")))
        text = (
            f"factgraph v{__version__} determinism audit\n"
            f"ordinary examples: {ordinary}\n"
            f"migration examples: {migrations}\n"
            f"semantic portability benchmark examples: {portability}\n"
            f"external semantic formats in determinism audit: 3 (Ossie, LinkML, Factum)\n"
            f"standalone metamodel bundles: 2\n"
            f"versioned metamodel/repository evidence: retained (v0.7 + v0.8 DAG/merge + v0.9 collaboration)\n"
            f"generated files per build: {len(fa)}\n"
            f"byte-identical: {len(keys) - len(mismatches)}\n"
            f"mismatches: {len(mismatches)}\n"
            f"result: {'PASS' if not mismatches and fa.keys() == fb.keys() else 'FAIL'}\n"
        )
        if mismatches:
            text += "mismatch paths:\n" + "\n".join(mismatches) + "\n"
        (ROOT / "artifacts" / "DETERMINISM_CHECK.txt").write_text(text, encoding="utf-8")
        print(text, end="")
        return 0 if "result: PASS" in text else 1


if __name__ == "__main__":
    raise SystemExit(main())
