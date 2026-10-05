import json
from pathlib import Path

from factgraph.audit import obligations, write_audit_bundle
from factgraph.importers.factum import import_factum
from factgraph.model import ConstraintKind
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.witness import synthesize_counterexample

ROOT = Path(__file__).resolve().parents[1]


def norm(text: str):
    return normalize_model(parse_model(text))


def obligation_for(model, *, kind: str, reading_contains: str | None = None):
    matches = [o for o in obligations(model) if o.kind == kind]
    if reading_contains:
        matches = [o for o in matches if reading_contains in o.reading]
    assert len(matches) == 1, [(o.id, o.kind, o.reading) for o in matches]
    return matches[0]


def test_duplicate_fact_counterexample_is_isolated():
    model = norm('''
model M {
  entity Person {}
  fact Seen(person: Person) {}
}
''')
    ob = obligation_for(model, kind="fact_set_semantics")
    result = synthesize_counterexample(model, ob.id, ob.kind)
    assert result.status == "isolated"
    assert result.target_violation_observed
    assert result.locally_irreducible
    assert [(v.obligation_id, v.code) for v in result.violations] == [(ob.id, "DUPLICATE_FACT")]


def test_full_tuple_uniqueness_is_reported_as_redundant_with_set_semantics():
    model = norm('''
model M {
  entity Person {}
  entity Company {}
  fact WorksFor(person: Person, company: Company) { unique(person, company) }
}
''')
    c = next(c for c in model.constraints.values() if c.kind == ConstraintKind.UNIQUENESS)
    result = synthesize_counterexample(model, c.id, "uniqueness")
    assert result.status == "not_independently_falsifiable"
    assert not result.target_violation_observed
    assert "set semantics" in result.note.lower()


def test_mandatory_and_symmetry_counterexamples_are_isolated():
    model = norm('''
model M {
  entity Person {}
  entity Company {}
  fact Employment(employee: Person, employer: Company) { mandatory(employee) }
  fact Knows(a: Person, b: Person) { symmetric }
}
''')
    mandatory = next(c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY)
    symmetry = next(c for c in model.constraints.values() if c.kind == ConstraintKind.RING)
    m = synthesize_counterexample(model, mandatory.id, "mandatory")
    s = synthesize_counterexample(model, symmetry.id, "ring")
    assert m.status == "isolated" and m.target_violation_observed and m.locally_irreducible
    assert s.status == "isolated" and s.target_violation_observed and s.locally_irreducible


def test_value_subset_and_exclusion_counterexamples_target_the_declared_rule():
    value_model = norm('''
model ValueM {
  value Score: Int { range(1, 5) }
}
''')
    value_c = next(c for c in value_model.constraints.values() if c.kind == ConstraintKind.VALUE)
    value_result = synthesize_counterexample(value_model, value_c.id, "value")
    assert value_result.status == "isolated"
    assert value_result.target_violation_observed

    set_model = norm('''
model SetM {
  entity Person {}
  fact A(person: Person) {}
  fact B(person: Person) {}
  fact C(person: Person) {}
  subset A(person) B(person)
  exclusion B(person) C(person)
}
''')
    for kind in (ConstraintKind.SUBSET, ConstraintKind.EXCLUSION):
        c = next(c for c in set_model.constraints.values() if c.kind == kind)
        result = synthesize_counterexample(set_model, c.id, kind.value)
        assert result.target_violation_observed
        assert result.status == "isolated"


def test_factum_audit_emits_source_semantic_counterexamples(tmp_path):
    fixture = ROOT / "examples" / "external" / "factum_people.orm.json"
    imported = import_factum(fixture.read_text())
    out = tmp_path / "factum-audit"
    report = write_audit_bundle(imported.model, out, source_import_report=imported.report)
    counts = report["semantic_counterexamples"]["counts"]
    assert sum(counts.values()) == report["summary"]["obligation_count"]
    assert counts["isolated"] > 0
    index = json.loads((out / "witnesses" / "index.json").read_text())
    assert len(index) == report["summary"]["obligation_count"]
    first = json.loads((out / index[0]["file"]).read_text())
    assert first["format"] == "factgraph-semantic-witness-recipe-v3"
    assert "source_counterexample" in first
    assert "## Source semantic counterexamples" in (out / "audit.md").read_text()
