from pathlib import Path

from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.validate import validate_model
from factgraph.analysis import evaluate_analyses
from factgraph.reporting import EvaluationState

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text()))


def test_examples_validate_cleanly():
    for name in ["warehouse.fg", "employment.fg", "recommendation.fg", "role_semantics.fg", "compound_identifier.fg", "mandatory_participation.fg", "richer_constraints.fg", "set_constraints.fg", "bson_value_gap.fg"]:
        assert validate_model(load(name)) == [], name


def test_unordered_samples_canonicalize_for_set_semantics():
    text = '''
model M {
  value PId: String
  entity Person { id id: PId }
  fact Partnership(a: Person, b: Person) { unordered(a, b) }
  sample Partnership("alice", "bob")
  sample Partnership("bob", "alice")
}
'''
    model = normalize_model(parse_model(text))
    codes = {d.code for d in validate_model(model)}
    assert "DUPLICATE_FACT" in codes


def test_declared_uniqueness_is_checked_on_samples():
    text = '''
model M {
  value Id: String
  entity A { id id: Id }
  entity B { id id: Id }
  fact F(a: A, b: B) { unique(a) }
  sample F("a1", "b1")
  sample F("a1", "b2")
}
'''
    model = normalize_model(parse_model(text))
    codes = {d.code for d in validate_model(model)}
    assert "UNIQUENESS_VIOLATION" in codes


def test_model_shape_analysis_is_separate_and_four_state():
    model = load("warehouse.fg")
    results = {r.name: r for r in evaluate_analyses(model)}
    assert results["connected"].state == EvaluationState.PASSED
    assert results["uniform(2)"].state == EvaluationState.VIOLATED


def test_v03_examples_validate_cleanly():
    assert validate_model(load("richer_constraints.fg")) == []
    assert validate_model(load("set_constraints.fg")) == []


def test_frequency_constraint_is_checked_on_sample_population():
    text = '''
model M {
  value Id: String
  entity A { id id: Id }
  entity B { id id: Id }
  fact F(a: A, b: B) { frequency(a, 0, 1) }
  sample F("a1", "b1")
  sample F("a1", "b2")
}
'''
    codes = {d.code for d in validate_model(normalize_model(parse_model(text)))}
    assert "FREQUENCY_VIOLATION" in codes


def test_value_constraint_is_checked_on_direct_value_role_samples():
    text = '''
model M {
  value Score: Int { range(0, 10) }
  entity A { }
  fact Rated(a: A, score: Score) { }
  sample Rated("a1", 11)
}
'''
    codes = {d.code for d in validate_model(normalize_model(parse_model(text)))}
    assert "VALUE_CONSTRAINT_VIOLATION" in codes


def test_subset_equality_and_exclusion_are_checked_on_samples():
    base = '''
model M {
  value Id: String
  entity A { id id: Id }
  fact F(a: A) { }
  fact G(a: A) { }
  fact H(a: A) { }
  subset F(a) G(a)
  equality G(a) H(a)
  exclusion F(a) H(a)
  sample F("x")
  sample G("y")
  sample H("y")
}
'''
    codes = {d.code for d in validate_model(normalize_model(parse_model(base)))}
    assert "SUBSET_VIOLATION" in codes
    assert "EXCLUSION_VIOLATION" not in codes
    assert "EQUALITY_VIOLATION" not in codes


def test_v03_frequency_min_does_not_imply_closed_world_mandatory_participation():
    text = '''
model M {
  value Id: String
  entity A { id id: Id }
  entity B { id id: Id }
  fact F(a: A, b: B) { frequency(a, 2, 3) }
}
'''
    # With no sample F rows there is no observed projection to count. This is
    # deliberately not a mandatory-participation violation.
    assert validate_model(normalize_model(parse_model(text))) == []


def test_v03_equality_and_exclusion_violations_are_reported_independently():
    text = '''
model M {
  value Id: String
  entity A { id id: Id }
  fact F(a: A) { }
  fact G(a: A) { }
  fact H(a: A) { }
  equality F(a) G(a)
  exclusion F(a) H(a)
  sample F("x")
  sample G("y")
  sample H("x")
}
'''
    codes = {d.code for d in validate_model(normalize_model(parse_model(text)))}
    assert "EQUALITY_VIOLATION" in codes
    assert "EXCLUSION_VIOLATION" in codes
