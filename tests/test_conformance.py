from pathlib import Path

from factgraph.conformance import bundle, mongo_cases, postgres_cases
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.roundtrip import report as roundtrip_report

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text()))


def test_every_native_capability_has_a_conformance_case():
    for name in [
        "warehouse.fg", "employment.fg", "recommendation.fg",
        "role_semantics.fg", "compound_identifier.fg", "mandatory_participation.fg",
        "richer_constraints.fg", "set_constraints.fg", "bson_value_gap.fg",
    ]:
        result = bundle(load(name))
        assert result["coverage"]["complete"], (name, result["coverage"]["uncovered_native_or_emulated"])
        assert result["static"]["all_passed"], name


def test_gap_probes_exist_for_reported_non_enforcement():
    m = load("mandatory_participation.fg")
    pg = postgres_cases(m)
    mg = mongo_cases(m)
    assert any(c.feature == "mandatory_not_enforced" for c in pg)
    assert any(c.feature == "mandatory_not_enforced" for c in mg)

    m = load("role_semantics.fg")
    pg = postgres_cases(m)
    mg = mongo_cases(m)
    assert any(c.feature == "ring:symmetric_not_enforced" for c in pg)
    assert any(c.feature == "ring:symmetric_not_enforced" for c in mg)


def test_roundtrip_report_distinguishes_structure_from_sidecar_recovery():
    for name in ["warehouse.fg", "employment.fg", "compound_identifier.fg", "richer_constraints.fg", "set_constraints.fg", "bson_value_gap.fg"]:
        r = roundtrip_report(load(name))
        assert r["all_current_invariants_pass"]
        assert r["postgres"]["structural_emitter_reader_consistency"]["native_exact_semantic_roundtrip_claimed"] is False
        assert r["mongo"]["structural_emitter_reader_consistency"]["native_exact_semantic_roundtrip_claimed"] is False
        assert r["postgres"]["sidecar_assisted_exact_recovery"] is True
        assert r["mongo"]["sidecar_assisted_exact_recovery"] is True


def test_generated_runtime_cases_have_explicit_expected_outcomes():
    m = load("role_semantics.fg")
    for case in [*postgres_cases(m), *mongo_cases(m)]:
        if case.mode != "runtime":
            continue
        assert case.steps
        for step in case.steps:
            assert "expect" in step or "expect_scalar" in step


def test_v03_native_capabilities_have_cases_and_non_enforced_set_constraints_have_gap_probes():
    m = load("richer_constraints.fg")
    result = bundle(m)
    assert result["coverage"]["complete"], result["coverage"]["uncovered_native_or_emulated"]
    pg = postgres_cases(m)
    mg = mongo_cases(m)
    assert any(c.feature == "value" for c in pg)
    assert any(c.feature == "value" for c in mg)
    assert any(c.feature == "frequency" for c in pg)
    assert any(c.feature == "frequency" for c in mg)
    assert any(c.feature == "subtype" for c in pg)
    assert any(c.feature == "subtype_not_enforced" for c in mg)

    m = load("set_constraints.fg")
    pg = postgres_cases(m)
    mg = mongo_cases(m)
    for feature in ["subset_not_enforced", "equality_not_enforced", "exclusion_not_enforced"]:
        assert any(c.feature == feature for c in pg)
        assert any(c.feature == feature for c in mg)


def test_v03_frequency_above_one_has_explicit_non_enforcement_probe():
    m = load("richer_constraints.fg")
    assert any(c.feature == "frequency_not_enforced" for c in postgres_cases(m))
    assert any(c.feature == "frequency_not_enforced" for c in mongo_cases(m))


def test_v03_mongo_bson_literal_value_gap_has_probe():
    text = '''
model M {
  value Amount: Decimal { range(0, 10) }
  entity E { amount: Amount }
}
'''
    m = normalize_model(parse_model(text))
    cases = mongo_cases(m)
    assert any(c.feature == "value_not_enforced" for c in cases)
    assert bundle(m)["coverage"]["complete"]
