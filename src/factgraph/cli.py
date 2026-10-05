from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

from .analysis import evaluate_analyses
from .incidence import IncidenceIndex
from .normalize import normalize_model
from .parser import parse_model
from .printer import print_model
from .reporting import analyses_json, diagnostics_json, Severity
from .validate import validate_model
from .targets import postgres, mongo, graphql, typedb
from . import conformance, roundtrip, audit as semantic_audit
from .diff import MigrationHints, semantic_diff
from .migration import plan_semantic_migration
from .migrations import postgres as pg_migration, mongo as mongo_migration
from . import metamodel
from .identity import identity_report
from .importers.ossie import import_ossie
from .importers.linkml import import_linkml
from .importers.factum import import_factum


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    return normalize_model(parse_model(text))

def _detect_external_input_format(text: str) -> str:
    """Detect the bounded external schema formats we intentionally support.

    Detection is structural rather than filename-based so a JSON LinkML schema is
    not silently interpreted as Ossie (and vice versa). Ambiguous inputs fail.
    """
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ValueError("auto-detecting YAML input requires PyYAML; install factgraph[interchange]") from exc
        raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("external model input must be a mapping/object")
    looks_ossie = isinstance(raw.get("ontology"), list)
    looks_linkml = any(key in raw for key in ("classes", "slots", "enums"))
    looks_factum = isinstance(raw.get("objectTypes"), list) and isinstance(raw.get("factTypes"), list)
    detected = [name for name, yes in (("ossie", looks_ossie), ("linkml", looks_linkml), ("factum", looks_factum)) if yes]
    if len(detected) > 1:
        raise ValueError(f"external model input is ambiguous between {', '.join(detected)}; pass --input-format explicitly")
    if detected:
        return detected[0]
    raise ValueError("could not auto-detect YAML/JSON model format; expected Apache Ossie, LinkML, or Factum ORM structure")


def load_input(path: Path, input_format: str = "factgraph"):
    """Load a model plus an optional external-format import report."""
    fmt = input_format
    text = path.read_text(encoding="utf-8")
    if fmt == "auto":
        fmt = _detect_external_input_format(text) if path.suffix.lower() in {".yaml", ".yml", ".json"} else "factgraph"
    if fmt == "factgraph":
        return normalize_model(parse_model(text)), None
    if fmt == "ossie":
        result = import_ossie(text)
        return result.model, result
    if fmt == "linkml":
        result = import_linkml(text)
        return result.model, result
    if fmt == "factum":
        result = import_factum(text)
        return result.model, result
    raise ValueError(f"unsupported input format {input_format!r}")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_reification(model, out_dir: Path, metamodel_version: str | None = None) -> dict:
    core = metamodel.encode_model(model, include_envelope=False, metamodel_version=metamodel_version)
    envelope = metamodel.encode_model(model, include_envelope=True, metamodel_version=metamodel_version)
    recovered_core = metamodel.decode_model(core, include_envelope=False, metamodel_version=metamodel_version)
    recovered_envelope = metamodel.decode_model(envelope, include_envelope=True, metamodel_version=metamodel_version)
    report = metamodel.reification_report(model, metamodel_version=metamodel_version)
    write(out_dir / "core.population.json", metamodel.population_json(core))
    write(out_dir / "envelope.population.json", metamodel.population_json(envelope))
    write(out_dir / "populated.metamodel.manifest.json", envelope.manifest_json())
    write(out_dir / "recovered.semantic.json", recovered_core.semantic_json(include_samples=False))
    write(out_dir / "recovered.manifest.json", recovered_envelope.manifest_json())
    write(out_dir / "recovered.normalized.fg", print_model(recovered_envelope))
    write(out_dir / "roundtrip.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def write_metamodel_bundle(out_dir: Path, version: str | None = None) -> dict:
    mm = metamodel.build_metamodel(version)
    validation = validate_model(mm)
    incidence = IncidenceIndex.build(mm)
    self_population = metamodel.encode_model(mm, include_envelope=True, metamodel_version=version)
    report = metamodel.self_host_report(version)
    write(out_dir / "metamodel.fg", print_model(mm))
    write(out_dir / "semantic.json", mm.semantic_json(include_samples=False))
    write(out_dir / "manifest.json", mm.manifest_json())
    write(out_dir / "validation.json", diagnostics_json(validation))
    write(out_dir / "identity_coverage.json", json.dumps(identity_report(mm), indent=2, sort_keys=True) + "\n")
    write(out_dir / "incidence.json", json.dumps({
        "roles_by_object": incidence.roles_by_object,
        "roles_by_fact": incidence.roles_by_fact,
    }, indent=2, sort_keys=True) + "\n")
    write(out_dir / "self.population.json", metamodel.population_json(self_population))
    write(out_dir / "self.populated.metamodel.manifest.json", self_population.manifest_json())
    write(out_dir / "self_host.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def build(input_path: Path, out_dir: Path) -> dict:
    model = load(input_path)
    validation = validate_model(model)
    analyses = evaluate_analyses(model)
    incidence = IncidenceIndex.build(model)

    write(out_dir / "normalized.fg", print_model(model))
    write(out_dir / "semantic.json", model.semantic_json(include_samples=False))
    write(out_dir / "manifest.json", model.manifest_json())
    write(out_dir / "validation.json", diagnostics_json(validation))
    write(out_dir / "identity_coverage.json", json.dumps(identity_report(model), indent=2, sort_keys=True) + "\n")
    write(out_dir / "analyses.json", analyses_json(analyses))
    write(
        out_dir / "incidence.json",
        json.dumps(
            {
                "roles_by_object": incidence.roles_by_object,
                "roles_by_fact": incidence.roles_by_fact,
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
    )

    # PostgreSQL
    pg_sql = postgres.emit_sql(model)
    write(out_dir / "postgres" / "model.sql", pg_sql)
    write(out_dir / "postgres" / "plan.json", postgres.build_plan(model).to_json())
    write(out_dir / "postgres" / "capabilities.json", postgres.capability_report(model).to_json())
    write(out_dir / "postgres" / "structure_recovery.json", postgres.structural_recovery_json(pg_sql))
    write(out_dir / "postgres" / "semantic.json", model.manifest_json())
    pg_recovered = postgres.recover_with_manifest(pg_sql, model.manifest_json())

    # MongoDB
    mongo_spec = mongo.emit_spec_json(model)
    write(out_dir / "mongo" / "model.js", mongo.emit_script(model))
    write(out_dir / "mongo" / "spec.json", mongo_spec)
    write(out_dir / "mongo" / "plan.json", mongo.emit_plan_json(model))
    write(out_dir / "mongo" / "capabilities.json", mongo.capability_report(model).to_json())
    write(out_dir / "mongo" / "structure_recovery.json", mongo.structural_recovery_json(mongo_spec))
    write(out_dir / "mongo" / "semantic.json", model.manifest_json())

    mongo_recovered = mongo.recover_with_manifest(mongo_spec, model.manifest_json())

    # GraphQL
    write(out_dir / "graphql" / "schema.graphql", graphql.emit_sdl(model))
    write(out_dir / "graphql" / "capabilities.json", graphql.capability_report(model).to_json())
    write(out_dir / "graphql" / "semantic.json", model.manifest_json())

    # TypeDB 3.x / TypeQL: a semantic contrast target with first-class n-ary relations.
    write(out_dir / "typedb" / "schema.tql", typedb.emit_schema(model))
    write(out_dir / "typedb" / "plan.json", typedb.emit_plan_json(model))
    write(out_dir / "typedb" / "capabilities.json", typedb.capability_report(model).to_json())
    write(out_dir / "typedb" / "semantic.json", model.manifest_json())

    # Conformance evidence: generated cases are target-executable; structural cases are evaluated locally.
    conf = conformance.bundle(model)
    write(out_dir / "conformance" / "postgres" / "cases.json", conformance.cases_json(conf["postgres_cases"]))
    write(out_dir / "conformance" / "mongo" / "cases.json", conformance.cases_json(conf["mongo_cases"]))
    write(out_dir / "conformance" / "typedb" / "cases.json", conformance.cases_json(conf["typedb_cases"]))
    write(out_dir / "conformance" / "coverage.json", json.dumps(conf["coverage"], indent=2, sort_keys=True) + "\n")
    write(out_dir / "conformance" / "static_results.json", json.dumps(conf["static"], indent=2, sort_keys=True) + "\n")
    write(
        out_dir / "conformance" / "live_status.json",
        json.dumps({
            "status": "not_run",
            "reason": "build generates executable cases but does not contact live services; use `factgraph live-conformance` with DSNs/URIs",
            "postgres_driver": "optional psycopg via factgraph[live] (also available in factgraph[conformance])",
            "mongo_driver": "optional pymongo via factgraph[live] (also available in factgraph[conformance])",
            "typedb_driver": "optional typedb-driver via factgraph[live] or factgraph[typedb]; live runner evidence remains separate from generated cases",
        }, indent=2, sort_keys=True) + "\n",
    )

    rt_report = roundtrip.report(model)
    write(out_dir / "roundtrip.json", json.dumps(rt_report, indent=2, sort_keys=True) + "\n")

    audit_report = semantic_audit.write_audit_bundle(model, out_dir / "audit", targets=("postgres", "mongo", "typedb"))
    from . import shared_witness_execution as shared_witness
    for target in ("postgres", "mongo", "typedb"):
        write(
            out_dir / "audit" / "shared_witness_execution" / f"{target}.plan.json",
            json.dumps(shared_witness.generated_report(model, target), indent=2, sort_keys=True) + "\n",
        )

    reify_report = write_reification(model, out_dir / "metamodel")

    error_count = sum(1 for d in validation if d.severity == Severity.ERROR)
    summary = {
        "input": str(input_path),
        "model": model.name,
        "validation_errors": error_count,
        "semantic_roundtrip": {
            "postgres_with_sidecar": model.semantically_equal(pg_recovered),
            "mongo_with_sidecar": model.semantically_equal(mongo_recovered),
        },
        "targets": ["postgres", "mongo", "typedb", "graphql"],
        "conformance": {
            "coverage_complete": conf["coverage"]["complete"],
            "structural_cases_pass": conf["static"]["all_passed"],
            "live_runtime_status": "not_run",
        },
        "roundtrip_invariants_pass": rt_report["all_current_invariants_pass"],
        "semantic_audit": {
            "obligation_count": audit_report["summary"]["obligation_count"],
            "targets": audit_report["targets"],
            "live_runtime_status": "not_run",
            "shared_witness_lowering": audit_report.get("shared_witness_lowering", {}).get("counts_by_target", {}),
        },
        "metamodel_reification": {
            "semantic_roundtrip_equal": reify_report["core_population"]["semantic_roundtrip_equal"],
            "manifest_roundtrip_equal": reify_report["envelope_population"]["manifest_roundtrip_equal"],
            "core_row_count": reify_report["core_population"]["row_count"],
            "envelope_row_count": reify_report["envelope_population"]["row_count"],
        },
        "notes": [
            "PostgreSQL and MongoDB structural recovery files intentionally report what can be read without semantic sidecars.",
            "Sidecar semantic.json files enable exact semantic recovery and are explicitly distinguished from native target structure.",
            "GraphQL is treated as an API/type-schema projection rather than a database backend.",
        ],
    }
    write(out_dir / "BUILD_SUMMARY.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary



def load_hints(path: Path | None) -> MigrationHints:
    if path is None:
        return MigrationHints.empty()
    return MigrationHints.from_json(path.read_text(encoding="utf-8"))


def build_migration(before_path: Path, after_path: Path, out_dir: Path, hints_path: Path | None = None) -> dict:
    before = load(before_path)
    after = load(after_path)
    hints = load_hints(hints_path)
    before_validation = validate_model(before)
    after_validation = validate_model(after)
    before_errors = sum(1 for d in before_validation if d.severity == Severity.ERROR)
    after_errors = sum(1 for d in after_validation if d.severity == Severity.ERROR)

    write(out_dir / "before" / "normalized.fg", print_model(before))
    write(out_dir / "before" / "semantic.json", before.semantic_json(include_samples=False))
    write(out_dir / "before" / "manifest.json", before.manifest_json())
    write(out_dir / "before" / "validation.json", diagnostics_json(before_validation))
    write(out_dir / "after" / "normalized.fg", print_model(after))
    write(out_dir / "after" / "semantic.json", after.semantic_json(include_samples=False))
    write(out_dir / "after" / "manifest.json", after.manifest_json())
    write(out_dir / "after" / "validation.json", diagnostics_json(after_validation))
    write(out_dir / "hints.json", json.dumps(hints.to_dict(), indent=2, sort_keys=True) + "\n")

    if before_errors or after_errors:
        summary = {
            "format": "factgraph-migration-build-v1",
            "before": str(before_path),
            "after": str(after_path),
            "before_validation_errors": before_errors,
            "after_validation_errors": after_errors,
            "status": "blocked_invalid_model",
        }
        write(out_dir / "MIGRATION_SUMMARY.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return summary

    diff = semantic_diff(before, after, hints)
    semantic_plan = plan_semantic_migration(diff)
    write(out_dir / "semantic_diff.json", diff.to_json())
    write(out_dir / "semantic_diff.md", diff.to_markdown())
    write(out_dir / "migration_plan.json", semantic_plan.to_json())
    write(out_dir / "migration_plan.md", semantic_plan.to_markdown())

    pg_plan = pg_migration.build_plan(before, after, hints)
    write(out_dir / "postgres" / "plan.json", pg_plan.to_json())
    write(out_dir / "postgres" / "plan.md", pg_plan.to_markdown())
    write(out_dir / "postgres" / "migration.sql", pg_migration.emit_safe_sql(pg_plan))
    write(out_dir / "postgres" / "migration.risky-preview.sql", pg_migration.emit_risky_preview_sql(pg_plan))
    write(out_dir / "postgres" / "migration.destructive-preview.sql", pg_migration.emit_destructive_preview_sql(pg_plan))
    write(out_dir / "postgres" / "preflight.sql", pg_migration.emit_preflight_sql(pg_plan))

    mg_plan = mongo_migration.build_plan(before, after, hints)
    write(out_dir / "mongo" / "plan.json", mg_plan.to_json())
    write(out_dir / "mongo" / "plan.md", mg_plan.to_markdown())
    write(out_dir / "mongo" / "migration.js", mongo_migration.emit_safe_script(mg_plan))
    write(out_dir / "mongo" / "migration.risky-preview.js", mongo_migration.emit_risky_preview_script(mg_plan))
    write(out_dir / "mongo" / "migration.destructive-preview.js", mongo_migration.emit_destructive_preview_script(mg_plan))
    write(out_dir / "mongo" / "preflight.js", mongo_migration.emit_preflight_script(mg_plan))
    write(
        out_dir / "live_execution_status.json",
        json.dumps({
            "status": "not_run",
            "reason": "migration planning never contacts a database; use `factgraph live-migrate` with a DSN/URI to execute inside an isolated harness namespace",
            "postgres_driver": "optional psycopg via factgraph[live] (also available in factgraph[conformance])",
            "mongo_driver": "optional pymongo via factgraph[live] (also available in factgraph[conformance])",
            "typedb_driver": "optional typedb-driver via factgraph[live] or factgraph[typedb]; live runner evidence remains separate from generated cases",
        }, indent=2, sort_keys=True) + "\n",
    )

    summary = {
        "format": "factgraph-migration-build-v1",
        "before": str(before_path),
        "after": str(after_path),
        "before_model": before.name,
        "after_model": after.name,
        "before_validation_errors": before_errors,
        "after_validation_errors": after_errors,
        "status": "planned",
        "semantic": diff.to_dict()["summary"],
        "postgres": pg_plan.to_dict()["summary"],
        "mongo": mg_plan.to_dict()["summary"],
        "safety_contract": {
            "migration.sql/migration.js": "safe automatic operations only; gated/manual/destructive operations remain commented/omitted",
            "risky-preview": "also renders requires-data-check commands executable, but still blocks manual/destructive operations",
            "destructive-preview": "renders destructive commands too; this is a preview artifact, not an automatic approval",
            "manual": "never made executable by the planner",
        },
    }
    write(out_dir / "MIGRATION_SUMMARY.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="factgraph", description="Semantic portability auditor and fact-oriented transformation toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="normalize, validate, and emit all target artifacts")
    p_build.add_argument("input", type=Path)
    p_build.add_argument("--out-dir", type=Path, required=True)

    p_audit = sub.add_parser("audit", help="audit semantic preservation across target transformations")
    p_audit.add_argument("input", type=Path)
    p_audit.add_argument("--targets", default="postgres,mongo,typedb", help="comma-separated targets (postgres,mongo,typedb)")
    p_audit.add_argument("--input-format", choices=["auto", "factgraph", "ossie", "linkml", "factum"], default="auto", help="source model format; auto structurally detects Apache Ossie, LinkML, or Factum ORM for YAML/JSON")
    p_audit.add_argument("--out-dir", type=Path, required=True)
    p_audit.add_argument("--postgres-dsn", default=None)
    p_audit.add_argument("--mongo-uri", default=None)
    p_audit.add_argument("--typedb-address", default=None, help="TypeDB server address, e.g. 127.0.0.1:51729")
    p_audit.add_argument("--typedb-username", default="admin")
    p_audit.add_argument("--typedb-password", default="password")
    p_audit.add_argument("--typedb-tls", action="store_true")
    p_audit.add_argument(
        "--shared-live-dir", type=Path, default=None,
        help="import fingerprint-validated shared semantic-witness live evidence from <dir>/<target>.json instead of reconnecting to those targets",
    )

    p_pg_map = sub.add_parser("postgres-map-template", help="write an explicit semantic-to-physical mapping template for an external PostgreSQL artifact")
    p_pg_map.add_argument("input", type=Path)
    p_pg_map.add_argument("--schema", type=Path, required=True, help="external PostgreSQL DDL to bind by SHA-256")
    p_pg_map.add_argument("--input-format", choices=["auto", "factgraph", "ossie", "linkml", "factum"], default="auto")
    p_pg_map.add_argument("--out", type=Path, required=True)

    p_pg_artifact = sub.add_parser("audit-postgres-artifact", help="audit an externally produced PostgreSQL schema using source-semantic witnesses and an explicit physical mapping")
    p_pg_artifact.add_argument("input", type=Path)
    p_pg_artifact.add_argument("--schema", type=Path, required=True)
    p_pg_artifact.add_argument("--mapping", type=Path, required=True)
    p_pg_artifact.add_argument("--input-format", choices=["auto", "factgraph", "ossie", "linkml", "factum"], default="auto")
    p_pg_artifact.add_argument("--out-dir", type=Path, required=True)
    p_pg_artifact.add_argument("--postgres-dsn", default=None, help="optional live PostgreSQL DSN; omitted means generate evidence/witnesses without claiming observation")

    p_import_ossie = sub.add_parser("import-ossie", help="import an Apache Ossie ontology into the Factgraph semantic kernel with explicit gap reporting")
    p_import_ossie.add_argument("input", type=Path)
    p_import_ossie.add_argument("--out-dir", type=Path, required=True)

    p_import_linkml = sub.add_parser("import-linkml", help="import a bounded LinkML schema into the Factgraph semantic kernel with explicit gap reporting")
    p_import_linkml.add_argument("input", type=Path)
    p_import_linkml.add_argument("--out-dir", type=Path, required=True)

    p_import_factum = sub.add_parser("import-factum", help="import a bounded Factum ORM .orm.json model into the Factgraph semantic kernel with explicit gap reporting")
    p_import_factum.add_argument("input", type=Path)
    p_import_factum.add_argument("--out-dir", type=Path, required=True)

    p_norm = sub.add_parser("normalize", help="print canonical normalized source")
    p_norm.add_argument("input", type=Path)
    p_norm.add_argument("--out", type=Path, required=True)

    p_val = sub.add_parser("validate", help="write validation diagnostics")
    p_val.add_argument("input", type=Path)
    p_val.add_argument("--out", type=Path, required=True)

    p_meta = sub.add_parser("metamodel", help="emit a versioned Factgraph metamodel and self-description evidence")
    p_meta.add_argument("--version", default=None)
    p_meta.add_argument("--out-dir", type=Path, required=True)

    p_meta_versions = sub.add_parser("metamodel-versions", help="list packaged Factgraph metamodel versions")
    p_meta_versions.add_argument("--out", type=Path, required=True)

    p_meta_diff = sub.add_parser("metamodel-diff", help="semantic-diff two packaged Factgraph metamodel versions")
    p_meta_diff.add_argument("from_version")
    p_meta_diff.add_argument("to_version")
    p_meta_diff.add_argument("--out-dir", type=Path, required=True)

    p_meta_migrate = sub.add_parser("metamodel-migrate", help="migrate a stored metamodel population between compatible versions")
    p_meta_migrate.add_argument("population", type=Path)
    p_meta_migrate.add_argument("--to-version", required=True)
    p_meta_migrate.add_argument("--core", action="store_true", help="use semantic-core equality instead of full envelope/manifest equality")
    p_meta_migrate.add_argument("--out-dir", type=Path, required=True)

    p_reify = sub.add_parser("reify", help="encode a model as a population of a versioned Factgraph metamodel and decode it back")
    p_reify.add_argument("input", type=Path)
    p_reify.add_argument("--metamodel-version", default=None)
    p_reify.add_argument("--out-dir", type=Path, required=True)

    p_repo_init = sub.add_parser("repo-init", help="initialize a content-verified Factgraph model repository")
    p_repo_init.add_argument("repository", type=Path)
    p_repo_init.add_argument("--name", default="FactgraphRepository")
    p_repo_init.add_argument("--metamodel-version", default=metamodel.CURRENT_METAMODEL_VERSION)
    p_repo_init.add_argument("--out", type=Path, default=None)

    p_repo_commit = sub.add_parser("repo-commit", help="commit a model revision to a Factgraph repository")
    p_repo_commit.add_argument("repository", type=Path)
    p_repo_commit.add_argument("input", type=Path)
    p_repo_commit.add_argument("--model-key", default=None)
    p_repo_commit.add_argument("--metamodel-version", default=None)
    p_repo_commit.add_argument("--branch", default="main")
    p_repo_commit.add_argument("--author", default=None)
    p_repo_commit.add_argument("--message", default=None)
    p_repo_commit.add_argument("--out", type=Path, required=True)

    p_repo_list = sub.add_parser("repo-list", help="list models and heads in a Factgraph repository")
    p_repo_list.add_argument("repository", type=Path)
    p_repo_list.add_argument("--out", type=Path, required=True)

    p_repo_log = sub.add_parser("repo-log", help="list immutable revisions for one repository model")
    p_repo_log.add_argument("repository", type=Path)
    p_repo_log.add_argument("model_key")
    p_repo_log.add_argument("--out", type=Path, required=True)

    p_repo_checkout = sub.add_parser("repo-checkout", help="copy one immutable repository revision to a file handoff directory")
    p_repo_checkout.add_argument("repository", type=Path)
    p_repo_checkout.add_argument("model_key")
    p_repo_checkout.add_argument("--revision", default=None)
    p_repo_checkout.add_argument("--out-dir", type=Path, required=True)

    p_repo_refs = sub.add_parser("repo-refs", help="list branches and immutable tags for one repository model")
    p_repo_refs.add_argument("repository", type=Path)
    p_repo_refs.add_argument("model_key")
    p_repo_refs.add_argument("--out", type=Path, required=True)

    p_repo_branch = sub.add_parser("repo-branch", help="create or delete a repository branch")
    p_repo_branch.add_argument("repository", type=Path)
    p_repo_branch.add_argument("model_key")
    p_repo_branch.add_argument("name")
    p_repo_branch.add_argument("--from-ref", default=None)
    p_repo_branch.add_argument("--delete", action="store_true")
    p_repo_branch.add_argument("--out", type=Path, required=True)

    p_repo_tag = sub.add_parser("repo-tag", help="create an immutable repository tag")
    p_repo_tag.add_argument("repository", type=Path)
    p_repo_tag.add_argument("model_key")
    p_repo_tag.add_argument("name")
    p_repo_tag.add_argument("--ref", default=None)
    p_repo_tag.add_argument("--out", type=Path, required=True)

    p_repo_keygen = sub.add_parser("repo-keygen", help="generate an Ed25519 keypair for revision attestations")
    p_repo_keygen.add_argument("--private-key", type=Path, required=True)
    p_repo_keygen.add_argument("--public-key", type=Path, required=True)
    p_repo_keygen.add_argument("--out", type=Path, required=True)

    p_repo_sign = sub.add_parser("repo-sign", help="append an Ed25519 attestation for an immutable revision")
    p_repo_sign.add_argument("repository", type=Path)
    p_repo_sign.add_argument("model_key")
    p_repo_sign.add_argument("--ref", default=None)
    p_repo_sign.add_argument("--private-key", type=Path, required=True)
    p_repo_sign.add_argument("--signer", default=None)
    p_repo_sign.add_argument("--out", type=Path, required=True)

    p_repo_sig_verify = sub.add_parser("repo-verify-signatures", help="verify revision attestations against registered public keys")
    p_repo_sig_verify.add_argument("repository", type=Path)
    p_repo_sig_verify.add_argument("--model-key", default=None)
    p_repo_sig_verify.add_argument("--out", type=Path, required=True)

    p_repo_trust_key = sub.add_parser("repo-trust-key", help="register a public key as explicitly trusted by this repository")
    p_repo_trust_key.add_argument("repository", type=Path)
    p_repo_trust_key.add_argument("public_key", type=Path)
    p_repo_trust_key.add_argument("--label", default=None)
    p_repo_trust_key.add_argument("--out", type=Path, required=True)

    p_repo_revoke_key = sub.add_parser("repo-revoke-key", help="revoke a previously trusted public key")
    p_repo_revoke_key.add_argument("repository", type=Path)
    p_repo_revoke_key.add_argument("key_id")
    p_repo_revoke_key.add_argument("--reason", default=None)
    p_repo_revoke_key.add_argument("--out", type=Path, required=True)

    p_repo_protect = sub.add_parser("repo-protect-branch", help="require trusted signatures before a branch may be advanced by remote push")
    p_repo_protect.add_argument("repository", type=Path)
    p_repo_protect.add_argument("model_key")
    p_repo_protect.add_argument("branch")
    p_repo_protect.add_argument("--min-signatures", type=int, default=1)
    p_repo_protect.add_argument("--key-id", action="append", default=[])
    p_repo_protect.add_argument("--remove", action="store_true")
    p_repo_protect.add_argument("--out", type=Path, required=True)

    p_repo_trust_policy = sub.add_parser("repo-trust-policy", help="show the repository trust policy")
    p_repo_trust_policy.add_argument("repository", type=Path)
    p_repo_trust_policy.add_argument("--out", type=Path, required=True)

    p_repo_trust_eval = sub.add_parser("repo-trust-evaluate", help="evaluate one revision against a protected-branch trust rule")
    p_repo_trust_eval.add_argument("repository", type=Path)
    p_repo_trust_eval.add_argument("model_key")
    p_repo_trust_eval.add_argument("--ref", default=None)
    p_repo_trust_eval.add_argument("--branch", default=None)
    p_repo_trust_eval.add_argument("--out", type=Path, required=True)

    p_repo_remotes = sub.add_parser("repo-remotes", help="list configured filesystem remotes")
    p_repo_remotes.add_argument("repository", type=Path)
    p_repo_remotes.add_argument("--out", type=Path, required=True)

    p_repo_remote = sub.add_parser("repo-remote", help="add or remove a filesystem repository remote")
    p_repo_remote.add_argument("repository", type=Path)
    p_repo_remote.add_argument("name")
    p_repo_remote.add_argument("path", type=Path, nargs="?")
    p_repo_remote.add_argument("--remove", action="store_true")
    p_repo_remote.add_argument("--out", type=Path, required=True)

    p_repo_fetch = sub.add_parser("repo-fetch", help="fetch verified immutable objects without advancing a local branch")
    p_repo_fetch.add_argument("repository", type=Path)
    p_repo_fetch.add_argument("remote")
    p_repo_fetch.add_argument("model_key")
    p_repo_fetch.add_argument("--branch", default="main")
    p_repo_fetch.add_argument("--out-dir", type=Path, required=True)

    p_repo_push = sub.add_parser("repo-push", help="content-addressed fast-forward push, subject to remote trust policy")
    p_repo_push.add_argument("repository", type=Path)
    p_repo_push.add_argument("remote")
    p_repo_push.add_argument("model_key")
    p_repo_push.add_argument("--branch", default="main")
    p_repo_push.add_argument("--out-dir", type=Path, required=True)

    p_repo_merge = sub.add_parser("repo-merge", help="three-way semantic merge two repository refs")
    p_repo_merge.add_argument("repository", type=Path)
    p_repo_merge.add_argument("model_key")
    p_repo_merge.add_argument("--ours", required=True)
    p_repo_merge.add_argument("--theirs", required=True)
    p_repo_merge.add_argument("--resolutions", type=Path, default=None)
    p_repo_merge.add_argument("--commit", action="store_true")
    p_repo_merge.add_argument("--author", default=None)
    p_repo_merge.add_argument("--message", default=None)
    p_repo_merge.add_argument("--out-dir", type=Path, required=True)

    p_repo_verify = sub.add_parser("repo-verify", help="verify repository hashes, histories, populations, and metamodel snapshots")
    p_repo_verify.add_argument("repository", type=Path)
    p_repo_verify.add_argument("--out", type=Path, required=True)

    p_repo_diff = sub.add_parser("repo-diff", help="semantic-diff two immutable revisions of one repository model")
    p_repo_diff.add_argument("repository", type=Path)
    p_repo_diff.add_argument("model_key")
    p_repo_diff.add_argument("before_revision")
    p_repo_diff.add_argument("after_revision")
    p_repo_diff.add_argument("--hints", type=Path, default=None)
    p_repo_diff.add_argument("--out-dir", type=Path, required=True)

    p_repo_mm = sub.add_parser("repo-migrate-metamodel", help="re-encode repository model heads under a new metamodel version")
    p_repo_mm.add_argument("repository", type=Path)
    p_repo_mm.add_argument("--to-version", required=True)
    p_repo_mm.add_argument("--model-key", default=None)
    p_repo_mm.add_argument("--out", type=Path, required=True)

    p_live = sub.add_parser("live-conformance", help="execute generated conformance cases against live PostgreSQL, MongoDB, and/or TypeDB")
    p_live.add_argument("input", type=Path)
    p_live.add_argument("--out-dir", type=Path, required=True)
    p_live.add_argument("--postgres-dsn", default=None)
    p_live.add_argument("--mongo-uri", default=None)
    p_live.add_argument("--typedb-address", default=None)
    p_live.add_argument("--typedb-username", default="admin")
    p_live.add_argument("--typedb-password", default="password")
    p_live.add_argument("--typedb-tls", action="store_true")

    p_diff = sub.add_parser("diff", help="compute a semantic diff between two model versions")
    p_diff.add_argument("before", type=Path)
    p_diff.add_argument("after", type=Path)
    p_diff.add_argument("--hints", type=Path, default=None)
    p_diff.add_argument("--out-dir", type=Path, required=True)

    p_migrate = sub.add_parser("migrate", help="plan semantic and target-specific schema migrations")
    p_migrate.add_argument("before", type=Path)
    p_migrate.add_argument("after", type=Path)
    p_migrate.add_argument("--hints", type=Path, default=None)
    p_migrate.add_argument("--out-dir", type=Path, required=True)

    p_live_migrate = sub.add_parser("live-migrate", help="execute and verify a planned migration inside isolated live PostgreSQL/MongoDB namespaces")
    p_live_migrate.add_argument("before", type=Path)
    p_live_migrate.add_argument("after", type=Path)
    p_live_migrate.add_argument("--hints", type=Path, default=None)
    p_live_migrate.add_argument("--fixture", type=Path, default=None)
    p_live_migrate.add_argument("--out-dir", type=Path, required=True)
    p_live_migrate.add_argument("--postgres-dsn", default=None)
    p_live_migrate.add_argument("--mongo-uri", default=None)
    p_live_migrate.add_argument("--allow-risky", action="store_true")
    p_live_migrate.add_argument("--allow-destructive", action="store_true")
    p_live_migrate.add_argument("--keep", action="store_true", help="keep isolated schema/database after the run for inspection")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "build":
            summary = build(args.input, args.out_dir)
            return 1 if summary["validation_errors"] else 0
        if args.cmd == "audit":
            model, imported = load_input(args.input, args.input_format)
            targets = [t.strip() for t in args.targets.split(",") if t.strip()]
            unsupported = sorted(set(targets) - {"postgres", "mongo", "typedb"})
            if unsupported:
                raise ValueError(f"unsupported audit target(s): {', '.join(unsupported)}")
            live_reports = {}
            if "postgres" in targets:
                if args.postgres_dsn:
                    from .live import postgres as live_postgres
                    live_reports["postgres"] = live_postgres.run(model, args.postgres_dsn)
                else:
                    live_reports["postgres"] = {"target": "postgres", "status": "not_run", "reason": "--postgres-dsn not provided"}
            if "mongo" in targets:
                if args.mongo_uri:
                    from .live import mongo as live_mongo
                    live_reports["mongo"] = live_mongo.run(model, args.mongo_uri)
                else:
                    live_reports["mongo"] = {"target": "mongo", "status": "not_run", "reason": "--mongo-uri not provided"}
            if "typedb" in targets:
                if args.typedb_address:
                    from .live import typedb as live_typedb
                    live_reports["typedb"] = live_typedb.run(
                        model, args.typedb_address, username=args.typedb_username,
                        password=args.typedb_password, tls=args.typedb_tls,
                    )
                else:
                    live_reports["typedb"] = {"target": "typedb", "status": "not_run", "reason": "--typedb-address not provided"}
            from . import shared_witness_execution as shared_witness
            shared_live_reports: dict[str, dict] = {}
            if "postgres" in targets:
                shared_live_reports["postgres"] = (
                    shared_witness.run_postgres(model, args.postgres_dsn)
                    if args.postgres_dsn else {**shared_witness.generated_report(model, "postgres"), "status": "not_run", "reason": "--postgres-dsn not provided"}
                )
            if "mongo" in targets:
                shared_live_reports["mongo"] = (
                    shared_witness.run_mongo(model, args.mongo_uri)
                    if args.mongo_uri else {**shared_witness.generated_report(model, "mongo"), "status": "not_run", "reason": "--mongo-uri not provided"}
                )
            if "typedb" in targets:
                shared_live_reports["typedb"] = (
                    shared_witness.run_typedb(
                        model, args.typedb_address, username=args.typedb_username,
                        password=args.typedb_password, tls=args.typedb_tls,
                    )
                    if args.typedb_address else {**shared_witness.generated_report(model, "typedb"), "status": "not_run", "reason": "--typedb-address not provided"}
                )
            evidence_import_report = None
            if args.shared_live_dir is not None:
                supplied_connections = {
                    "postgres": bool(args.postgres_dsn),
                    "mongo": bool(args.mongo_uri),
                    "typedb": bool(args.typedb_address),
                }
                ambiguous = [t for t in targets if supplied_connections.get(t) and (args.shared_live_dir / f"{t}.json").exists()]
                if ambiguous:
                    raise ValueError(
                        "shared live evidence and direct live connection were both supplied for: " + ", ".join(ambiguous)
                    )
                from .evidence import load_shared_live_directory
                imported_shared, evidence_import_report = load_shared_live_directory(args.shared_live_dir, model, targets)
                shared_live_reports.update(imported_shared)
                write(args.out_dir / "evidence_import.json", json.dumps(evidence_import_report, indent=2, sort_keys=True) + "\n")

            for target, shared_report in shared_live_reports.items():
                write(args.out_dir / "shared_witness_execution" / f"{target}.json", json.dumps(shared_report, indent=2, sort_keys=True) + "\n")
            if imported is not None:
                write(args.out_dir / "source_import" / "import_report.json", json.dumps(imported.report, indent=2, sort_keys=True) + "\n")
                write(args.out_dir / "source_import" / "normalized.fg", imported.generated_source)
                write(args.out_dir / "source_import" / "source.txt", args.input.read_text(encoding="utf-8"))
            report = semantic_audit.write_audit_bundle(
                model, args.out_dir, targets=targets, live_reports=live_reports,
                source_import_report=(imported.report if imported is not None else None),
                shared_live_reports=shared_live_reports,
            )
            validation_errors = sum(1 for d in validate_model(model) if d.severity == Severity.ERROR)
            shared_fail = any(r.get("status") == "completed" and r.get("passed") is False for r in shared_live_reports.values())
            return 1 if validation_errors or shared_fail or any(
                ob["verdicts"][t]["preservation"] == "claim_falsified"
                for ob in report["obligations"] for t in targets
            ) else 0
        if args.cmd == "postgres-map-template":
            model, imported = load_input(args.input, args.input_format)
            artifact_sql = args.schema.read_text(encoding="utf-8")
            from . import postgres_artifact
            mapping = postgres_artifact.mapping_template(model, artifact_sql)
            write(args.out, json.dumps(mapping, indent=2, sort_keys=True) + "\n")
            return 0
        if args.cmd == "audit-postgres-artifact":
            model, imported = load_input(args.input, args.input_format)
            artifact_sql = args.schema.read_text(encoding="utf-8")
            mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
            if not isinstance(mapping, dict):
                raise ValueError("external PostgreSQL mapping must be a JSON object")
            from . import postgres_artifact
            report = postgres_artifact.write_bundle(model, artifact_sql, mapping, args.out_dir, dsn=args.postgres_dsn)
            if imported is not None:
                write(args.out_dir / "source_import" / "import_report.json", json.dumps(imported.report, indent=2, sort_keys=True) + "\n")
                write(args.out_dir / "source_import" / "normalized.fg", imported.generated_source)
                write(args.out_dir / "source_import" / "source.txt", args.input.read_text(encoding="utf-8"))
            return 1 if report.get("status") in {"invalid_mapping", "artifact_setup_failed", "artifact_structure_mismatch"} else 0
        if args.cmd == "import-ossie":
            result = import_ossie(args.input.read_text(encoding="utf-8"))
            write(args.out_dir / "normalized.fg", result.generated_source)
            write(args.out_dir / "semantic.json", result.model.semantic_json(include_samples=False))
            write(args.out_dir / "manifest.json", result.model.manifest_json())
            write(args.out_dir / "import_report.json", json.dumps(result.report, indent=2, sort_keys=True) + "\n")
            write(args.out_dir / "validation.json", diagnostics_json(validate_model(result.model)))
            return 1 if result.report.get("status") == "failed" else 0
        if args.cmd == "import-linkml":
            result = import_linkml(args.input.read_text(encoding="utf-8"))
            write(args.out_dir / "normalized.fg", result.generated_source)
            write(args.out_dir / "semantic.json", result.model.semantic_json(include_samples=False))
            write(args.out_dir / "manifest.json", result.model.manifest_json())
            write(args.out_dir / "import_report.json", json.dumps(result.report, indent=2, sort_keys=True) + "\n")
            write(args.out_dir / "validation.json", diagnostics_json(validate_model(result.model)))
            return 1 if result.report.get("status") == "failed" else 0
        if args.cmd == "import-factum":
            result = import_factum(args.input.read_text(encoding="utf-8"))
            write(args.out_dir / "normalized.fg", result.generated_source)
            write(args.out_dir / "semantic.json", result.model.semantic_json(include_samples=False))
            write(args.out_dir / "manifest.json", result.model.manifest_json())
            write(args.out_dir / "import_report.json", json.dumps(result.report, indent=2, sort_keys=True) + "\n")
            write(args.out_dir / "validation.json", diagnostics_json(validate_model(result.model)))
            return 1 if result.report.get("status") == "failed" else 0
        if args.cmd == "normalize":
            write(args.out, print_model(load(args.input)))
            return 0
        if args.cmd == "validate":
            model = load(args.input)
            diagnostics = validate_model(model)
            write(args.out, diagnostics_json(diagnostics))
            return 1 if any(d.severity == Severity.ERROR for d in diagnostics) else 0
        if args.cmd == "metamodel":
            report = write_metamodel_bundle(args.out_dir, args.version)
            return 0 if report["semantic_roundtrip_equal"] and report["manifest_roundtrip_equal"] and report["second_encoding_identical"] else 1
        if args.cmd == "metamodel-versions":
            write(args.out, json.dumps({"format": "factgraph-metamodel-version-index-v1", "current_version": metamodel.CURRENT_METAMODEL_VERSION, "versions": metamodel.metamodel_versions()}, indent=2, sort_keys=True) + "\n")
            return 0
        if args.cmd == "metamodel-diff":
            diff = metamodel.metamodel_semantic_diff(args.from_version, args.to_version)
            write(args.out_dir / "semantic_diff.json", diff.to_json())
            write(args.out_dir / "semantic_diff.md", diff.to_markdown())
            write(args.out_dir / "from.metamodel.fg", metamodel.metamodel_source(args.from_version))
            write(args.out_dir / "to.metamodel.fg", metamodel.metamodel_source(args.to_version))
            write(args.out_dir / "SUMMARY.json", json.dumps({"format": "factgraph-metamodel-diff-v1", "from_version": args.from_version, "to_version": args.to_version, "diff": diff.to_dict()["summary"]}, indent=2, sort_keys=True) + "\n")
            return 0
        if args.cmd == "metamodel-migrate":
            populated = metamodel.population_from_json(args.population.read_text(encoding="utf-8"))
            migrated, report = metamodel.migration_report(populated, target_version=args.to_version, include_envelope=not args.core)
            recovered = metamodel.decode_model(migrated, include_envelope=not args.core, metamodel_version=args.to_version)
            write(args.out_dir / "source.population.json", metamodel.population_json(populated))
            write(args.out_dir / "migrated.population.json", metamodel.population_json(migrated))
            write(args.out_dir / "recovered.normalized.fg", print_model(recovered))
            write(args.out_dir / "recovered.manifest.json", recovered.manifest_json())
            write(args.out_dir / "migration.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
            return 0 if report["migration_passed"] else 1
        if args.cmd == "reify":
            model = load(args.input)
            report = write_reification(model, args.out_dir, args.metamodel_version)
            return 0 if report["core_population"]["semantic_roundtrip_equal"] and report["envelope_population"]["manifest_roundtrip_equal"] else 1
        if args.cmd.startswith("repo-"):
            from . import repository as model_repository
            if args.cmd == "repo-init":
                result = model_repository.init_repository(args.repository, name=args.name, default_metamodel_version=args.metamodel_version)
                if args.out:
                    write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-commit":
                result = model_repository.commit_model(
                    args.repository,
                    args.input,
                    model_key=args.model_key,
                    metamodel_version=args.metamodel_version,
                    branch=args.branch,
                    author=args.author,
                    message=args.message,
                )
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-list":
                write(args.out, json.dumps(model_repository.list_models(args.repository), indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-log":
                write(args.out, json.dumps(model_repository.log_model(args.repository, args.model_key), indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-checkout":
                result = model_repository.checkout_revision(args.repository, args.model_key, args.out_dir, revision_id=args.revision)
                write(args.out_dir / "CHECKOUT.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-refs":
                result = model_repository.list_refs(args.repository, args.model_key)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-branch":
                result = (
                    model_repository.delete_branch(args.repository, args.model_key, args.name)
                    if args.delete
                    else model_repository.create_branch(args.repository, args.model_key, args.name, from_ref=args.from_ref)
                )
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-tag":
                result = model_repository.create_tag(args.repository, args.model_key, args.name, ref=args.ref)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-keygen":
                from . import signing
                result = signing.generate_keypair(args.private_key, args.public_key)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-sign":
                result = model_repository.sign_revision(
                    args.repository,
                    args.model_key,
                    ref=args.ref,
                    private_key_path=args.private_key,
                    signer=args.signer,
                )
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-verify-signatures":
                result = model_repository.verify_signatures(args.repository, model_key=args.model_key)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0 if result["passed"] else 1
            if args.cmd == "repo-trust-key":
                result = model_repository.trust_public_key(args.repository, args.public_key, label=args.label)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-revoke-key":
                result = model_repository.revoke_trusted_key(args.repository, args.key_id, reason=args.reason)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-protect-branch":
                result = (
                    model_repository.unprotect_branch(args.repository, args.model_key, args.branch)
                    if args.remove
                    else model_repository.protect_branch(
                        args.repository, args.model_key, args.branch,
                        min_valid_signatures=args.min_signatures, allowed_key_ids=args.key_id,
                    )
                )
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-trust-policy":
                result = model_repository.load_trust_policy(args.repository)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-trust-evaluate":
                result = model_repository.evaluate_revision_trust(
                    args.repository, args.model_key, args.ref, branch=args.branch
                )
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0 if result["passed"] else 1
            if args.cmd in {"repo-remotes", "repo-remote", "repo-fetch", "repo-push"}:
                from . import remote as repository_remote
                if args.cmd == "repo-remotes":
                    result = repository_remote.list_remotes(args.repository)
                    write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                    return 0
                if args.cmd == "repo-remote":
                    if args.remove:
                        result = repository_remote.remove_remote(args.repository, args.name)
                    else:
                        if args.path is None:
                            raise ValueError("repo-remote requires PATH unless --remove is used")
                        result = repository_remote.add_remote(args.repository, args.name, args.path)
                    write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                    return 0
                if args.cmd == "repo-fetch":
                    repository_remote.fetch(
                        args.repository, args.remote, args.model_key, branch=args.branch, out_dir=args.out_dir
                    )
                    return 0
                result = repository_remote.push(
                    args.repository, args.remote, args.model_key, branch=args.branch, out_dir=args.out_dir
                )
                return 0
            if args.cmd == "repo-merge":
                resolutions = {}
                if args.resolutions is not None:
                    raw = json.loads(args.resolutions.read_text(encoding="utf-8"))
                    resolutions = raw.get("conflicts", raw)
                result = model_repository.merge_refs(
                    args.repository,
                    args.model_key,
                    ours=args.ours,
                    theirs=args.theirs,
                    resolutions=resolutions,
                    commit=args.commit,
                    author=args.author,
                    message=args.message,
                )
                write(args.out_dir / "merge.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
                base_for_explain = model_repository.load_revision_model(args.repository, args.model_key, result["base_revision_id"])
                ours_for_explain = model_repository.load_revision_model(args.repository, args.model_key, result["ours"]["revision_id"])
                theirs_for_explain = model_repository.load_revision_model(args.repository, args.model_key, result["theirs"]["revision_id"])
                from .merge import semantic_merge as _semantic_merge_for_explanation
                explained = _semantic_merge_for_explanation(base_for_explain, ours_for_explain, theirs_for_explain, resolutions=resolutions)
                write(args.out_dir / "merge.explanation.md", explained.to_markdown())
                merge_info = result["merge"]
                if merge_info.get("status") == "merged":
                    merged_model = model_repository.load_revision_model(
                        args.repository,
                        args.model_key,
                        result["commit"]["revision_id"],
                    ) if result.get("commit") else None
                    if merged_model is None:
                        base = model_repository.load_revision_model(args.repository, args.model_key, result["base_revision_id"])
                        ours_model = model_repository.load_revision_model(args.repository, args.model_key, result["ours"]["revision_id"])
                        theirs_model = model_repository.load_revision_model(args.repository, args.model_key, result["theirs"]["revision_id"])
                        from .merge import semantic_merge
                        merged_model = semantic_merge(base, ours_model, theirs_model, resolutions=resolutions).merged_model
                    if merged_model is not None:
                        write(args.out_dir / "merged.fg", print_model(merged_model))
                        write(args.out_dir / "merged.manifest.json", merged_model.manifest_json())
                return 0 if merge_info.get("status") == "merged" else 2
            if args.cmd == "repo-verify":
                result = model_repository.verify_repository(args.repository)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0 if result["passed"] else 1
            if args.cmd == "repo-diff":
                hints = load_hints(args.hints)
                diff = model_repository.diff_revisions(args.repository, args.model_key, args.before_revision, args.after_revision, hints=hints)
                write(args.out_dir / "semantic_diff.json", diff.to_json())
                write(args.out_dir / "semantic_diff.md", diff.to_markdown())
                write(args.out_dir / "hints.json", json.dumps(hints.to_dict(), indent=2, sort_keys=True) + "\n")
                return 0
            if args.cmd == "repo-migrate-metamodel":
                result = model_repository.migrate_repository_metamodel(args.repository, target_version=args.to_version, model_key=args.model_key)
                write(args.out, json.dumps(result, indent=2, sort_keys=True) + "\n")
                return 0 if result["repository_verification_passed"] else 1
        if args.cmd == "diff":
            before = load(args.before)
            after = load(args.after)
            hints = load_hints(args.hints)
            diff = semantic_diff(before, after, hints)
            write(args.out_dir / "semantic_diff.json", diff.to_json())
            write(args.out_dir / "semantic_diff.md", diff.to_markdown())
            write(args.out_dir / "hints.json", json.dumps(hints.to_dict(), indent=2, sort_keys=True) + "\n")
            return 0
        if args.cmd == "migrate":
            summary = build_migration(args.before, args.after, args.out_dir, args.hints)
            return 1 if summary.get("before_validation_errors") or summary.get("after_validation_errors") else 0
        if args.cmd == "live-migrate":
            before = load(args.before)
            after = load(args.after)
            hints = load_hints(args.hints)
            from .live import MigrationExecutionPolicy
            policy = MigrationExecutionPolicy(allow_risky=args.allow_risky, allow_destructive=args.allow_destructive)
            args.out_dir.mkdir(parents=True, exist_ok=True)
            reports = {}
            if args.postgres_dsn:
                from .live import postgres_migration as live_pg_migration
                reports["postgres"] = live_pg_migration.run(before, after, args.postgres_dsn, hints=hints, fixture_path=args.fixture, policy=policy, keep=args.keep)
            else:
                reports["postgres"] = {"target": "postgres", "status": "not_run", "reason": "--postgres-dsn not provided", "operations": []}
            write(args.out_dir / "postgres.json", json.dumps(reports["postgres"], indent=2, sort_keys=True, default=str) + "\n")
            if args.mongo_uri:
                from .live import mongo_migration as live_mg_migration
                reports["mongo"] = live_mg_migration.run(before, after, args.mongo_uri, hints=hints, fixture_path=args.fixture, policy=policy, keep=args.keep)
            else:
                reports["mongo"] = {"target": "mongo", "status": "not_run", "reason": "--mongo-uri not provided", "operations": []}
            write(args.out_dir / "mongo.json", json.dumps(reports["mongo"], indent=2, sort_keys=True, default=str) + "\n")
            bad = {"preflight_failed", "execution_failed", "verification_failed"}
            summary = {
                "format": "factgraph-live-migration-summary-v1",
                "before_model": before.name,
                "after_model": after.name,
                "policy": policy.to_dict(),
                "fixture": str(args.fixture) if args.fixture else None,
                "reports": {k: {
                    "status": v.get("status"),
                    "expected_complete": v.get("expected_complete"),
                    "verification_passed": v.get("verification", {}).get("passed"),
                    "namespace": v.get("namespace"),
                } for k, v in reports.items()},
                "failed_targets": [k for k, v in reports.items() if v.get("status") in bad],
            }
            write(args.out_dir / "LIVE_MIGRATION.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
            return 1 if summary["failed_targets"] else 0
        if args.cmd == "live-conformance":
            model = load(args.input)
            args.out_dir.mkdir(parents=True, exist_ok=True)
            reports = {}
            if args.postgres_dsn:
                from .live import postgres as live_postgres
                reports["postgres"] = live_postgres.run(model, args.postgres_dsn)
                write(args.out_dir / "postgres.json", json.dumps(reports["postgres"], indent=2, sort_keys=True) + "\n")
            else:
                reports["postgres"] = {"target": "postgres", "status": "not_run", "reason": "--postgres-dsn not provided"}
                write(args.out_dir / "postgres.json", json.dumps(reports["postgres"], indent=2, sort_keys=True) + "\n")
            if args.mongo_uri:
                from .live import mongo as live_mongo
                reports["mongo"] = live_mongo.run(model, args.mongo_uri)
                write(args.out_dir / "mongo.json", json.dumps(reports["mongo"], indent=2, sort_keys=True) + "\n")
            else:
                reports["mongo"] = {"target": "mongo", "status": "not_run", "reason": "--mongo-uri not provided"}
                write(args.out_dir / "mongo.json", json.dumps(reports["mongo"], indent=2, sort_keys=True) + "\n")
            if args.typedb_address:
                from .live import typedb as live_typedb
                reports["typedb"] = live_typedb.run(
                    model, args.typedb_address, username=args.typedb_username,
                    password=args.typedb_password, tls=args.typedb_tls,
                )
            else:
                reports["typedb"] = {"target": "typedb", "status": "not_run", "reason": "--typedb-address not provided"}
            write(args.out_dir / "typedb.json", json.dumps(reports["typedb"], indent=2, sort_keys=True) + "\n")
            completed = [r for r in reports.values() if r.get("status") == "completed"]
            requested_targets = [
                target for target, supplied in (
                    ("postgres", bool(args.postgres_dsn)),
                    ("mongo", bool(args.mongo_uri)),
                    ("typedb", bool(args.typedb_address)),
                ) if supplied
            ]
            failed_targets = [
                target for target in requested_targets
                if reports[target].get("status") != "completed" or not reports[target].get("passed", False)
            ]
            summary = {
                "model": model.name,
                "reports": reports,
                "requested_targets": requested_targets,
                "completed_targets": [r["target"] for r in completed],
                "failed_requested_targets": failed_targets,
                "all_requested_completed_and_passed": not failed_targets if requested_targets else None,
                "all_completed_passed": all(r.get("passed", False) for r in completed) if completed else None,
            }
            write(args.out_dir / "LIVE_CONFORMANCE.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
            return 1 if failed_targets else 0
    except Exception as exc:
        print(f"factgraph: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
