from factgraph.model import ConstraintKind
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.population import SemanticPopulation, validate_population


def norm(text: str):
    return normalize_model(parse_model(text))


def test_population_oracle_catches_total_participation_without_a_fact():
    model = norm('''
model M {
  entity Person {}
  entity Company {}
  fact Employment(employee: Person, employer: Company) {
    mandatory(employee)
  }
}
''')
    person = model.object_type_by_name("Person")
    mandatory = next(c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY)
    pop = SemanticPopulation()
    pop.add_membership(person.id, "p1")
    violations = validate_population(model, pop)
    assert [(v.obligation_id, v.code) for v in violations] == [(mandatory.id, "MANDATORY_VIOLATION")]


def test_population_oracle_catches_subtype_population_inclusion():
    model = norm('''
model M {
  entity Person {}
  entity Employee {}
  subtype Employee is Person
}
''')
    employee = model.object_type_by_name("Employee")
    subtype = next(c for c in model.constraints.values() if c.kind == ConstraintKind.SUBTYPE)
    pop = SemanticPopulation()
    pop.add_membership(employee.id, "e1")
    violations = validate_population(model, pop)
    assert [(v.obligation_id, v.code) for v in violations] == [(subtype.id, "SUBTYPE_VIOLATION")]


def test_population_oracle_catches_logical_symmetry_not_unorderedness():
    model = norm('''
model M {
  entity Person {}
  fact Knows(a: Person, b: Person) { symmetric }
}
''')
    fact = model.fact_by_name("Knows")
    person = model.object_type_by_name("Person")
    ring = next(c for c in model.constraints.values() if c.kind == ConstraintKind.RING)
    pop = SemanticPopulation()
    pop.add_membership(person.id, "p1")
    pop.add_membership(person.id, "p2")
    pop.add_fact(fact.id, ("p1", "p2"))
    violations = validate_population(model, pop)
    assert [(v.obligation_id, v.code) for v in violations] == [(ring.id, "SYMMETRY_VIOLATION")]


def test_preferred_identifier_only_compares_complete_identifier_tuples():
    model = norm('''
model M {
  value PersonId: String
  entity Person { id id: PersonId }
}
''')
    person = model.object_type_by_name("Person")
    mandatory = next(c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY)
    preferred = next(c for c in model.constraints.values() if c.kind == ConstraintKind.PREFERRED_IDENTIFIER)
    pop = SemanticPopulation()
    pop.add_membership(person.id, "p1")
    violations = validate_population(model, pop)
    assert any(v.obligation_id == mandatory.id and v.code == "MANDATORY_VIOLATION" for v in violations)
    assert not any(v.obligation_id == preferred.id for v in violations)
