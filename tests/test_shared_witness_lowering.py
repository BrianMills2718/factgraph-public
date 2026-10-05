from __future__ import annotations

import json
from pathlib import Path

from factgraph.audit import obligations, write_audit_bundle
from factgraph.cli import load
from factgraph.witness import synthesize_counterexample
from factgraph.witness_lowering import lower_population, render_program


ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "examples" / "portability"


def _witness(model_name: str, kind: str, contains: str | None = None):
    model = load(PORT / model_name)
    ob = next(o for o in obligations(model) if o.kind == kind and (contains is None or contains in o.id))
    result = synthesize_counterexample(model, ob.id, ob.kind)
    assert result.status == "isolated"
    return model, ob, result


def test_same_total_participation_population_lowers_to_three_targets():
    model, _ob, result = _witness("total_participation.fg", "mandatory", "Employment")
    pg = lower_population(model, result.population, "postgres")
    mg = lower_population(model, result.population, "mongo")
    td = lower_population(model, result.population, "typedb")
    assert pg.status == mg.status == td.status == "lowered"
    assert "INSERT INTO person (person_id) VALUES ('v2');" in render_program(pg)
    assert '"collection": "person"' in render_program(mg)
    assert "isa fg-e-person" in render_program(td)
    # Crucially, none of these exact programs invents the missing Employment fact.
    assert "employment" not in render_program(pg).lower()
    assert '"employment"' not in render_program(mg).lower()
    assert "fg-r-employment" not in render_program(td)


def test_duplicate_fact_multiplicity_is_preserved_in_target_programs():
    model, _ob, result = _witness("nary_set_semantics.fg", "fact_set_semantics")
    for target in ("postgres", "mongo", "typedb"):
        program = lower_population(model, result.population, target)
        assert program.status == "lowered"
    pg = render_program(lower_population(model, result.population, "postgres"))
    assert pg.count("INSERT INTO") >= 2
    fact_name = next(f.name for f in model.fact_types.values() if f.id not in model.field_hints)
    assert pg.lower().count(f"insert into {fact_name.lower()}") == 2
    td = render_program(lower_population(model, result.population, "typedb"))
    assert td.count("isa fg-r-") == 2


def test_scalar_field_two_value_witness_is_not_faked_in_postgres_or_mongo():
    model, _ob, result = _witness("simple_identifier.fg", "uniqueness")
    pg = lower_population(model, result.population, "postgres")
    mg = lower_population(model, result.population, "mongo")
    td = lower_population(model, result.population, "typedb")
    assert pg.status == "representation_prevents_exact_realization"
    assert mg.status == "representation_prevents_exact_realization"
    assert td.status == "lowered"
    assert "2 values" in " ".join(pg.limitations)
    assert "has fg-a-person-email" in render_program(td)
    assert render_program(td).count("has fg-a-person-email") == 2


def test_standalone_value_counterexample_requires_contextual_embedding():
    model, _ob, result = _witness("value_range.fg", "value")
    for target in ("postgres", "mongo", "typedb"):
        program = lower_population(model, result.population, target)
        assert program.status == "unsupported"
        assert "context" in program.fidelity or "standalone" in " ".join(program.limitations)


def test_objectification_is_explicitly_unsupported_until_occurrence_identity_exists():
    model, _ob, result = _witness("objectification.fg", "fact_set_semantics", "Employment")
    for target in ("postgres", "mongo", "typedb"):
        program = lower_population(model, result.population, target)
        assert program.status == "unsupported"
        text = " ".join(program.limitations).lower()
        assert "objectified" in text and "identity" in text


def test_audit_bundle_writes_shared_lowering_files(tmp_path: Path):
    model = load(PORT / "total_participation.fg")
    report = write_audit_bundle(model, tmp_path, targets=("postgres", "mongo", "typedb"))
    assert report["shared_witness_lowering"]["counts_by_target"]["typedb"]["lowered"] > 0
    index = json.loads((tmp_path / "witnesses" / "index.json").read_text())
    assert index and all("shared_lowerings" in row for row in index)
    first = index[0]
    witness = json.loads((tmp_path / first["file"]).read_text())
    assert witness["format"] == "factgraph-semantic-witness-recipe-v3"
    lowerings = witness["shared_target_lowerings"]
    for target, native_suffix in (("postgres", "postgres.sql"), ("mongo", "mongo.operations.json"), ("typedb", "typedb.tql")):
        assert (tmp_path / lowerings[target]["metadata_file"]).exists()
        assert (tmp_path / lowerings[target]["native_program_file"]).exists()
        assert lowerings[target]["native_program_file"].endswith(native_suffix)
    assert lowerings["postgres"]["metadata_file"].endswith("postgres.json")


def test_missing_identifier_plus_relationship_is_a_target_shape_gap_not_an_unknown_lowering():
    model, ob, result = _witness("total_participation.fg", "mandatory", "Person__personId")
    assert "Person__personId" in ob.id
    for target in ("postgres", "mongo"):
        program = lower_population(model, result.population, target)
        assert program.status == "representation_prevents_exact_realization"
        assert program.fidelity == "identifier_projection_requires_missing_source_value"
    assert lower_population(model, result.population, "typedb").status == "lowered"
