from pathlib import Path

from factgraph.cli import load
from factgraph.model import ConstraintKind
from factgraph.reporting import CapabilityStatus
from factgraph.targets import typedb
from factgraph import conformance


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _caps(model):
    return {e.source_element: e for e in typedb.capability_report(model).entries}


def test_typedb_emits_nary_relation_with_exactly_one_player_per_object_role():
    model = load(EXAMPLES / "warehouse.fg")
    schema = typedb.emit_schema(model)
    assert "relation fg-r-stocking" in schema
    assert "relates part @card(1)" in schema
    assert "relates bin @card(1)" in schema
    assert "relates warehouse @card(1)" in schema
    # Complete-tuple uniqueness is not falsely inferred from relation structure.
    fact = model.fact_by_name("Stocking")
    assert _caps(model)[fact.id].status == CapabilityStatus.REPRESENTED_NOT_ENFORCED


def test_typedb_total_participation_maps_to_plays_lower_bound():
    model = load(EXAMPLES / "mandatory_participation.fg")
    fact = model.fact_by_name("Employment")
    mandatory = next(c for c in model.constraints_for_fact(fact.id) if c.kind == ConstraintKind.MANDATORY)
    schema = typedb.emit_schema(model)
    assert "plays fg-r-employment:employee @card(1..)" in schema
    assert _caps(model)[mandatory.id].status == CapabilityStatus.NATIVE_ENFORCED


def test_typedb_compound_identifier_is_not_overclaimed_while_single_key_is_native():
    model = load(EXAMPLES / "compound_identifier.fg")
    caps = _caps(model)
    ids = [c for c in model.constraints.values() if c.kind == ConstraintKind.PREFERRED_IDENTIFIER]
    statuses = {model.object_types[c.object_type_id].name: caps[c.id].status for c in ids}
    assert statuses["Employee"] == CapabilityStatus.REPRESENTED_NOT_ENFORCED
    assert statuses["Project"] == CapabilityStatus.NATIVE_ENFORCED
    schema = typedb.emit_schema(model)
    assert "fg-a-project-code @key" in schema
    assert "@subkey" not in schema


def test_typedb_value_constraints_and_subtyping_are_native_where_projected():
    model = load(EXAMPLES / "richer_constraints.fg")
    schema = typedb.emit_schema(model)
    assert '@range(0..1000)' in schema
    assert '@values("a001", "m002", "z003")' in schema
    assert "entity fg-e-employee sub fg-e-person" in schema
    caps = _caps(model)
    for c in model.constraints.values():
        if c.kind in {ConstraintKind.VALUE, ConstraintKind.SUBTYPE}:
            assert caps[c.id].status == CapabilityStatus.NATIVE_ENFORCED


def test_typedb_frequency_semantics_do_not_strengthen_lower_bounds():
    model = load(EXAMPLES / "richer_constraints.fg")
    caps = _caps(model)
    freqs = [c for c in model.constraints.values() if c.kind == ConstraintKind.FREQUENCY]
    by_min = {c.min_frequency: caps[c.id] for c in freqs}
    assert by_min[0].status == CapabilityStatus.NATIVE_ENFORCED
    assert by_min[1].status == CapabilityStatus.REPRESENTED_NOT_ENFORCED


def test_typedb_native_capability_coverage_is_complete_and_static_checks_pass():
    for path in sorted(EXAMPLES.glob("*.fg")):
        model = load(path)
        cases = conformance.typedb_cases(model)
        static = conformance.static_results(cases)
        coverage = conformance.coverage_report(model, typedb_case_list=cases)
        assert static["all_passed"], path.name
        assert coverage["targets"]["typedb"]["covered"] == coverage["targets"]["typedb"]["native_or_emulated_capabilities"], path.name
