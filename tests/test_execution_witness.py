from __future__ import annotations

from pathlib import Path

from factgraph.audit import obligations
from factgraph.cli import load
from factgraph.execution_witness import prepare_execution_witness
from factgraph.population import validate_population
from factgraph.shared_witness_execution import build_cases
from factgraph.witness import synthesize_counterexample

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "examples" / "portability"


def _value_obligation(model):
    return next(o for o in obligations(model) if o.kind == "value")


def test_standalone_value_counterexample_gets_one_shared_source_context():
    model = load(PORT / "value_range.fg")
    ob = _value_obligation(model)
    source = synthesize_counterexample(model, ob.id, ob.kind)
    assert sum(len(rows) for rows in source.population.facts.values()) == 0

    execution = prepare_execution_witness(model, source)
    assert execution.mode == "contextualized_source_core"
    assert execution.context_added is True
    assert execution.added_fact_row_count >= 1
    violations = validate_population(model, execution.population)
    assert {v.obligation_id for v in violations} == {ob.id}


def test_same_contextualized_value_population_is_lowerable_to_all_three_targets():
    model = load(PORT / "value_range.fg")
    ob = _value_obligation(model)
    cases = {
        target: next(c for c in build_cases(model, target) if c.obligation_id == ob.id)
        for target in ("postgres", "mongo", "typedb")
    }
    assert {c.execution_witness_mode for c in cases.values()} == {"contextualized_source_core"}
    populations = [c.execution_population for c in cases.values()]
    assert populations[0] == populations[1] == populations[2]
    assert all(c.lowering_status == "lowered" for c in cases.values())


def test_objectified_value_context_uses_occurrence_identity_and_lowers_to_all_targets():
    model = load(PORT / "objectification.fg")
    ob = _value_obligation(model)
    cases = [next(c for c in build_cases(model, t) if c.obligation_id == ob.id) for t in ("postgres", "mongo", "typedb")]
    assert all(c.execution_context_added for c in cases)
    assert all(c.execution_population["format"] == "factgraph-semantic-population-v2" for c in cases)
    assert all(c.execution_population.get("objectifications") for c in cases)
    assert all(c.lowering_status == "lowered" for c in cases)


def test_objectified_fact_and_reference_witnesses_share_bound_occurrences():
    model = load(PORT / "objectification.fg")
    for needle in ("obligation:set:fact:Employment", "obligation:set:fact:ManagedBy", "constraint:mandatory:EmploymentRecord__salary:owner"):
        rows = [next(c for c in build_cases(model, t) if needle in c.obligation_id) for t in ("postgres", "mongo", "typedb")]
        assert all(c.execution_population.get("objectifications") for c in rows)
        assert all(c.lowering_status == "lowered" for c in rows)


def test_objectified_multivalue_field_exposes_target_shape_difference():
    model = load(PORT / "objectification.fg")
    needle = "constraint:unique:EmploymentRecord__salary:owner"
    pg = next(c for c in build_cases(model, "postgres") if c.obligation_id == needle)
    mg = next(c for c in build_cases(model, "mongo") if c.obligation_id == needle)
    td = next(c for c in build_cases(model, "typedb") if c.obligation_id == needle)
    assert pg.lowering_status == mg.lowering_status == "representation_prevents_exact_realization"
    assert td.lowering_status == "lowered"
    assert td.expected_write_outcome == "prevented"
