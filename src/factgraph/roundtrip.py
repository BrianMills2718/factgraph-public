from __future__ import annotations

from typing import Any

from .model import Model
from .normalize import normalize_model
from .parser import parse_model
from .printer import print_model
from .targets import mongo, postgres


def source_roundtrip(model: Model) -> dict[str, Any]:
    canonical = print_model(model)
    recovered = normalize_model(parse_model(canonical))
    return {
        "passed": model.semantically_equal(recovered),
        "contract": "normalize(parse(print(model))) == model under semantic equality",
        "canonical_source_bytes": len(canonical.encode("utf-8")),
    }


def postgres_structural_consistency(model: Model) -> dict[str, Any]:
    plan = postgres.build_plan(model)
    recovered = postgres.structural_recovery(postgres.emit_sql(model))
    by_name = {t["name"]: t for t in recovered["tables"]}
    expected_names = [t.name for t in plan.tables]
    name_match = sorted(expected_names) == sorted(by_name)
    column_match = True
    details = []
    for table in plan.tables:
        actual = by_name.get(table.name)
        expected_cols = [c.name for c in table.columns]
        actual_cols = [c["name"] for c in actual["columns"]] if actual else []
        ok = expected_cols == actual_cols
        column_match = column_match and ok
        details.append({"table": table.name, "columns_match": ok, "expected": expected_cols, "actual": actual_cols})
    return {
        "passed": name_match and column_match,
        "table_names_match": name_match,
        "tables": details,
        "native_exact_semantic_roundtrip_claimed": False,
        "losses_reported_by_reader": recovered["not_reliably_recoverable_without_metadata"],
    }


def mongo_structural_consistency(model: Model) -> dict[str, Any]:
    spec = mongo.emit_spec_json(model)
    recovered = mongo.structural_recovery(spec)
    import json
    expected = json.loads(spec)["collections"]
    actual = recovered["collections"]
    return {
        "passed": expected == actual,
        "pure_target_spec_matches_reader_surface": expected == actual,
        "native_exact_semantic_roundtrip_claimed": False,
        "losses_reported_by_reader": recovered["not_reliably_recoverable_without_metadata"],
    }


def report(model: Model) -> dict[str, Any]:
    pg_sql = postgres.emit_sql(model)
    mongo_spec = mongo.emit_spec_json(model)
    pg_sidecar = postgres.recover_with_manifest(pg_sql, model.manifest_json())
    mongo_sidecar = mongo.recover_with_manifest(mongo_spec, model.manifest_json())
    pg_struct = postgres_structural_consistency(model)
    mg_struct = mongo_structural_consistency(model)
    src = source_roundtrip(model)
    return {
        "model": model.name,
        "source_canonical_roundtrip": src,
        "postgres": {
            "structural_emitter_reader_consistency": pg_struct,
            "sidecar_assisted_exact_recovery": model.semantically_equal(pg_sidecar),
            "sidecar_note": "Exact recovery uses the explicit semantic sidecar; it is not evidence that executable SQL alone preserves all conceptual semantics.",
        },
        "mongo": {
            "structural_emitter_reader_consistency": mg_struct,
            "sidecar_assisted_exact_recovery": model.semantically_equal(mongo_sidecar),
            "sidecar_note": "Exact recovery uses the explicit semantic sidecar; it is not evidence that the pure Mongo target spec alone preserves all conceptual semantics.",
        },
        "cross_target": {
            "sidecar_recovered_models_equal": pg_sidecar.semantically_equal(mongo_sidecar),
            "native_cross_target_lossless_claimed": False,
        },
        "all_current_invariants_pass": (
            src["passed"] and pg_struct["passed"] and mg_struct["passed"]
            and model.semantically_equal(pg_sidecar) and model.semantically_equal(mongo_sidecar)
        ),
    }
