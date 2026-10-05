#!/usr/bin/env python3
"""Determinism audit for the active v0.18 semantic-portability surface.

Historical v0.7-v0.9 repository/collaboration demos remain covered by their unit
regressions and archived release evidence. This audit intentionally focuses on
artifacts whose determinism is relevant to the current product thesis.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph import __version__, audit as semantic_audit  # noqa: E402
from factgraph.cli import build, load  # noqa: E402
from factgraph.importers.linkml import import_linkml  # noqa: E402
from factgraph.importers.factum import import_factum  # noqa: E402
from factgraph.importers.ossie import import_ossie  # noqa: E402
from factgraph.mutations import write_mutation_catalog  # noqa: E402
from factgraph.printer import print_model  # noqa: E402
from factgraph.targets import mongo, postgres, typedb  # noqa: E402
from factgraph.postgres_artifact import write_bundle as write_postgres_artifact_bundle  # noqa: E402


def file_hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_tree(dst: Path) -> None:
    # Retained compiler surface that ordinary users can still invoke.
    for src in sorted((ROOT / "examples").glob("*.fg")):
        build(src, dst / "ordinary" / src.stem)

    # Active semantic-portability benchmark.
    models = {}
    expectations = json.loads((ROOT / "examples" / "portability" / "expectations.json").read_text(encoding="utf-8"))
    write(dst / "portability" / "expectations.json", json.dumps(expectations, indent=2, sort_keys=False) + "\n")
    for filename in sorted(expectations["cases"]):
        src = ROOT / "examples" / "portability" / filename
        model = load(src)
        models[src.stem] = model
        out = dst / "portability" / "cases" / src.stem
        write(out / "normalized.fg", print_model(model))
        semantic_audit.write_audit_bundle(model, out / "audit", targets=("postgres", "mongo", "typedb"))
        write(out / "postgres" / "model.sql", postgres.emit_sql(model))
        write(out / "postgres" / "capabilities.json", postgres.capability_report(model).to_json())
        write(out / "mongo" / "spec.json", mongo.emit_spec_json(model))
        write(out / "mongo" / "capabilities.json", mongo.capability_report(model).to_json())
        write(out / "typedb" / "schema.tql", typedb.emit_schema(model))
        write(out / "typedb" / "capabilities.json", typedb.capability_report(model).to_json())
    write_mutation_catalog(models, dst / "portability" / "mutations")

    # External-format normalization/audit must also be deterministic.
    external = [
        ("ossie", ROOT / "examples" / "external" / "ossie_people.yaml", import_ossie),
        ("linkml", ROOT / "examples" / "external" / "linkml_people.yaml", import_linkml),
        ("factum", ROOT / "examples" / "external" / "factum_people.orm.json", import_factum),
    ]
    for name, source, importer in external:
        result = importer(source.read_text(encoding="utf-8"))
        out = dst / "external" / name
        write(out / "source.txt", source.read_text(encoding="utf-8"))
        write(out / "normalized.fg", result.generated_source)
        write(out / "import_report.json", json.dumps(result.report, indent=2, sort_keys=True) + "\n")
        semantic_audit.write_audit_bundle(result.model, out / "audit", targets=("postgres", "mongo", "typedb"))

    # v0.14 external PostgreSQL artifact mapping/audit remains part of the active product surface.
    pg_external_root = ROOT / "examples" / "external_targets" / "postgres"
    pg_external_model = load(pg_external_root / "source_value_range.fg")
    for name in ("value_range_preserved", "value_range_weakened"):
        source_dir = pg_external_root / name
        artifact_sql = (source_dir / "schema.sql").read_text(encoding="utf-8")
        mapping = json.loads((source_dir / "mapping.json").read_text(encoding="utf-8"))
        write_postgres_artifact_bundle(
            pg_external_model, artifact_sql, mapping, dst / "external_postgres_artifacts" / name
        )

    # v0.15-v0.18 pinned third-party pipeline source/provenance and source-semantic audit.
    # The actual LinkML-generated DDL is deliberately produced only by the pinned
    # external tool in hosted CI, so determinism here covers the local reproducible
    # handoff rather than fabricating third-party output.
    linkml_pipeline_root = ROOT / "examples" / "external_pipelines" / "linkml_1_11_1"
    pipeline_source = linkml_pipeline_root / "value_range.yaml"
    pipeline_result = import_linkml(pipeline_source.read_text(encoding="utf-8"))
    pipeline_out = dst / "external_pipelines" / "linkml_1_11_1"
    write(pipeline_out / "source.yaml", pipeline_source.read_text(encoding="utf-8"))
    write(pipeline_out / "normalized.fg", pipeline_result.generated_source)
    write(pipeline_out / "import_report.json", json.dumps(pipeline_result.report, indent=2, sort_keys=True) + "\n")
    write(pipeline_out / "PROVENANCE.json", (linkml_pipeline_root / "PROVENANCE.json").read_text(encoding="utf-8"))
    semantic_audit.write_audit_bundle(pipeline_result.model, pipeline_out / "source_audit", targets=("postgres",))
    write(pipeline_out / "LOCAL_READINESS.json", json.dumps({
        "status": "generator_not_invoked_in_determinism_audit",
        "pinned_external_pipeline": "linkml==1.11.1 / gen-sqltables --dialect postgresql",
        "live_observed": False,
    }, indent=2, sort_keys=True) + "\n")

    # Factum ORM 0.5.0 -> PostgreSQL stress source plus v0.18 upstream provenance-locked control.
    factum_pipeline_root = ROOT / "examples" / "external_pipelines" / "factum_0_5_0"
    factum_source = factum_pipeline_root / "mandatory_bridge.orm.json"
    factum_result = import_factum(factum_source.read_text(encoding="utf-8"))
    factum_out = dst / "external_pipelines" / "factum_0_5_0"
    write(factum_out / "source.orm.json", factum_source.read_text(encoding="utf-8"))
    write(factum_out / "normalized.fg", factum_result.generated_source)
    write(factum_out / "import_report.json", json.dumps(factum_result.report, indent=2, sort_keys=True) + "\n")
    write(factum_out / "PROVENANCE.json", (factum_pipeline_root / "PROVENANCE.json").read_text(encoding="utf-8"))
    semantic_audit.write_audit_bundle(factum_result.model, factum_out / "source_audit", targets=("postgres",))
    write(factum_out / "LOCAL_READINESS.json", json.dumps({
        "status": "generator_not_invoked_in_determinism_audit",
        "pinned_external_pipeline": "Factum ORM 0.5.0 exact Git commit / bundled factum.js / factum ddl --dialect postgres",
        "live_observed": False,
    }, indent=2, sort_keys=True) + "\n")

    upstream_root = factum_pipeline_root / "upstream_fig_mandatory"
    upstream_out = factum_out / "upstream_fig_mandatory"
    write(upstream_out / "PROVENANCE.json", (upstream_root / "PROVENANCE.json").read_text(encoding="utf-8"))
    write(upstream_out / "LOCAL_READINESS.json", json.dumps({
        "status": "exact_upstream_checkouts_not_invoked_in_determinism_audit",
        "generator_commit": "7897dd0c4303b9eea46c60342f1632b27a624744",
        "model_commit": "e5990315e9a2a7905d129c5626cbde09dab2a842",
        "source_model_vendored": False,
        "live_observed": False,
    }, indent=2, sort_keys=True) + "\n")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="factgraph-v018-det-a-") as a, tempfile.TemporaryDirectory(prefix="factgraph-v018-det-b-") as b:
        pa, pb = Path(a), Path(b)
        build_tree(pa)
        build_tree(pb)
        fa, fb = file_hashes(pa), file_hashes(pb)
        keys = sorted(set(fa) | set(fb))
        mismatches = [k for k in keys if fa.get(k) != fb.get(k)]
        text = (
            f"factgraph v{__version__} active-surface determinism audit\n"
            f"ordinary compiler examples: {len(list((ROOT / 'examples').glob('*.fg')))}\n"
            f"semantic portability benchmark examples: {len(list((ROOT / 'examples' / 'portability').glob('*.fg')))}\n"
            "external semantic formats: 3 (Ossie, LinkML, Factum)\n"
            "mutation catalog: included\n"
            "external PostgreSQL artifact audit examples: 2 (preserved/weakened value range; generated-only)\n"
            "pinned third-party pipeline experiments: 3 (LinkML range, Factum-authored m:n stress, upstream Factum mandatory control; 2 external tool families; live generation delegated to CI)\n"
            f"generated files per build: {len(fa)}\n"
            f"byte-identical: {len(keys) - len(mismatches)}\n"
            f"mismatches: {len(mismatches)}\n"
            f"result: {'PASS' if not mismatches and fa.keys() == fb.keys() else 'FAIL'}\n"
            "scope: active v0.18 compiler/audit/acceptance-probe/target/import/shared-witness/mutation/external-artifact/provenance-locked third-party-pipeline handoff artifacts; frozen repository/collaboration demos are regression-tested separately\n"
        )
        if mismatches:
            text += "mismatch paths:\n" + "\n".join(mismatches) + "\n"
        (ROOT / "artifacts" / "DETERMINISM_CHECK.txt").write_text(text, encoding="utf-8")
        print(text, end="")
        return 0 if "result: PASS" in text else 1


if __name__ == "__main__":
    raise SystemExit(main())
