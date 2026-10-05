from __future__ import annotations

from pathlib import Path

from factgraph.cli import load
from factgraph.shared_witness_execution import build_cases, generated_report, _evaluate

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "examples" / "portability"


def _case(model_name: str, target: str, needle: str):
    model = load(PORT / model_name)
    return next(c for c in build_cases(model, target) if needle in c.obligation_id)


def test_total_participation_has_opposite_write_oracles_for_postgres_and_typedb():
    pg = _case("total_participation.fg", "postgres", "mandatory:Employment:employee")
    td = _case("total_participation.fg", "typedb", "mandatory:Employment:employee")
    assert pg.lowering_status == td.lowering_status == "lowered"
    assert pg.expected_write_outcome == "realized"
    assert pg.poststate_required_for_semantic_proof is True
    assert td.expected_write_outcome == "prevented"
    assert td.poststate_required_for_semantic_proof is False


def test_fact_set_semantics_shared_witness_oracle_differs_by_target():
    pg = _case("nary_set_semantics.fg", "postgres", "obligation:set:")
    mg = _case("nary_set_semantics.fg", "mongo", "obligation:set:")
    td = _case("nary_set_semantics.fg", "typedb", "obligation:set:")
    assert pg.expected_write_outcome == "prevented"
    assert mg.expected_write_outcome == "prevented"
    assert td.expected_write_outcome == "realized"


def test_scalar_field_multivalue_is_not_executable_for_document_or_relational_projection():
    pg = _case("simple_identifier.fg", "postgres", "constraint:unique:")
    mg = _case("simple_identifier.fg", "mongo", "constraint:unique:")
    td = _case("simple_identifier.fg", "typedb", "constraint:unique:")
    assert pg.expected_write_outcome == mg.expected_write_outcome == "not_executable"
    assert td.expected_write_outcome == "prevented"


def test_subtype_case_exposes_three_distinct_target_semantics():
    pg = _case("subtype.fg", "postgres", "constraint:subtype:")
    mg = _case("subtype.fg", "mongo", "constraint:subtype:")
    td = _case("subtype.fg", "typedb", "constraint:subtype:")

    assert pg.lowering_status == "lowered"
    assert pg.lowering_fidelity == "source_core_with_target_support_identity"
    assert pg.expected_write_outcome == "prevented"

    assert mg.lowering_status == "lowered"
    assert mg.lowering_fidelity == "source_core_with_target_support_identity"
    assert mg.expected_write_outcome == "realized"
    assert mg.poststate_required_for_semantic_proof is True
    assert mg.postconditions and mg.postconditions[0]["op"] == "count_documents"
    assert mg.postconditions[0]["collection"] == "person"

    assert td.lowering_status == "representation_prevents_exact_realization"
    assert td.lowering_fidelity == "native_subtype_entailment_prevents_invalid_membership"
    assert td.expected_write_outcome == "not_executable"


def test_evaluation_does_not_promote_acceptance_when_poststate_is_required():
    case = _case("total_participation.fg", "postgres", "mandatory:Employment:employee")
    assert case.postconditions
    assert case.postconditions[0]["op"] == "scalar_sql"
    row = _evaluate(case, "realized")
    assert row["passed"] is None
    assert row["semantic_evidence_level"] == "write_realization_observed_poststate_pending"
    assert row["semantic_observation"] == "poststate_pending"
    assert row["observed_preservation"] is None

    verified = _evaluate(
        case, "realized", poststate_passed=True,
        poststate_results=[{"op": "scalar_sql", "actual_scalar": 0, "expected_scalar": 0, "passed": True}],
    )
    assert verified["passed"] is True
    assert verified["semantic_evidence_level"] == "invalid_population_state_verified"
    assert verified["semantic_observation"] == "invalid_population_realized"
    assert verified["observed_preservation"] == "weakened"

    contradicted = _evaluate(
        case, "realized", poststate_passed=False,
        poststate_results=[{"op": "scalar_sql", "actual_scalar": 1, "expected_scalar": 0, "passed": False}],
    )
    assert contradicted["passed"] is False
    assert contradicted["semantic_evidence_level"] == "write_accepted_but_invalid_state_not_realized"
    assert contradicted["semantic_observation"] == "invalid_population_not_realized_after_write"
    assert contradicted["observed_preservation"] == "preserved_or_stronger"


def test_generated_report_is_file_ready_and_non_live():
    model = load(PORT / "total_participation.fg")
    report = generated_report(model, "typedb")
    assert report["status"] == "generated_not_run"
    assert report["executable_case_count"] > 0
    assert report["case_count"] == len(build_cases(model, "typedb"))


def test_poststate_queries_are_generated_for_absence_based_weakened_rules():
    expectations = [
        ("total_participation.fg", "mandatory:Employment:employee", {"postgres", "mongo"}),
        ("set_constraints.fg", "constraint:subset:", {"postgres", "mongo", "typedb"}),
        ("set_constraints.fg", "constraint:equality:", {"postgres", "mongo", "typedb"}),
        ("symmetry.fg", "constraint:ring:", {"postgres", "mongo", "typedb"}),
    ]
    for filename, needle, targets in expectations:
        for target in targets:
            case = _case(filename, target, needle)
            assert case.expected_write_outcome == "realized"
            assert case.poststate_required_for_semantic_proof is True
            assert case.postconditions, (filename, target, case.obligation_id)


def test_generated_report_counts_all_required_ready_poststate_queries_for_current_cases():
    for filename in ("total_participation.fg", "set_constraints.fg", "symmetry.fg", "subtype.fg"):
        model = load(PORT / filename)
        for target in ("postgres", "mongo", "typedb"):
            report = generated_report(model, target)
            assert report["poststate_query_ready_count"] == report["poststate_required_count"]
