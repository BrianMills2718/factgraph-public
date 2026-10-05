from pathlib import Path

from factgraph.model import ConstraintKind
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.printer import print_model
from factgraph.incidence import IncidenceIndex

ROOT = Path(__file__).resolve().parents[1]


def load_example(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text()))


def test_parse_print_normalize_is_semantically_idempotent():
    for name in ["warehouse.fg", "employment.fg", "recommendation.fg", "role_semantics.fg", "compound_identifier.fg", "mandatory_participation.fg", "richer_constraints.fg", "set_constraints.fg", "bson_value_gap.fg"]:
        m1 = load_example(name)
        text = print_model(m1)
        m2 = normalize_model(parse_model(text))
        assert m1.semantically_equal(m2), name


def test_repeated_player_types_remain_distinct_roles():
    m = load_example("recommendation.fg")
    fact = m.fact_by_name("Recommendation")
    assert [r.name for r in fact.roles] == ["recommender", "candidate", "job"]
    assert fact.roles[0].player_id == fact.roles[1].player_id
    assert fact.roles[0].id != fact.roles[1].id
    idx = IncidenceIndex.build(m)
    assert fact.roles[0].id in idx.roles_by_object[fact.roles[0].player_id]
    assert fact.roles[1].id in idx.roles_by_object[fact.roles[1].player_id]


def test_unordered_and_symmetric_are_not_conflated():
    m = load_example("role_semantics.fg")
    partnership = m.fact_by_name("Partnership")
    knows = m.fact_by_name("Knows")
    assert any(c.kind == ConstraintKind.UNORDERED_ROLE_GROUP for c in m.constraints_for_fact(partnership.id))
    assert not any(c.kind == ConstraintKind.RING for c in m.constraints_for_fact(partnership.id))
    rings = [c for c in m.constraints_for_fact(knows.id) if c.kind == ConstraintKind.RING]
    assert len(rings) == 1 and rings[0].ring_kind == "symmetric"
    assert not any(c.kind == ConstraintKind.UNORDERED_ROLE_GROUP for c in m.constraints_for_fact(knows.id))


def test_relationship_fields_desugar_through_objectification():
    m = load_example("employment.fg")
    fact = m.fact_by_name("Employment")
    obj = m.objectification_for_fact(fact.id)
    assert obj is not None and obj.name == "EmploymentRecord"
    hints = [h for h in m.field_hints.values() if h.owner_object_type_id == obj.id]
    assert {h.field_name for h in hints} == {"salary", "startDate"}
    assert all(h.field_fact_id in m.fact_types for h in hints)


def test_entity_fields_are_fact_sugar_and_compound_id_is_one_preferred_identifier():
    m = load_example("compound_identifier.fg")
    emp = m.object_type_by_name("Employee")
    hints = [h for h in m.field_hints.values() if h.owner_object_type_id == emp.id]
    assert {h.field_name for h in hints} == {"firstName", "lastName"}
    assert all(h.identifier_component for h in hints)
    ids = [c for c in m.constraints.values() if c.kind == ConstraintKind.PREFERRED_IDENTIFIER and c.object_type_id == emp.id]
    assert len(ids) == 1
    assert len(ids[0].field_fact_ids) == 2


def test_v03_richer_constraints_survive_parse_print_normalize():
    for name in ["richer_constraints.fg", "set_constraints.fg", "bson_value_gap.fg"]:
        m1 = load_example(name)
        m2 = normalize_model(parse_model(print_model(m1)))
        assert m1.semantically_equal(m2), name


def test_frequency_value_set_and_subtype_constraints_are_first_class():
    m = load_example("richer_constraints.fg")
    assert any(c.kind == ConstraintKind.FREQUENCY for c in m.constraints.values())
    assert len([c for c in m.constraints.values() if c.kind == ConstraintKind.VALUE]) == 2
    sub = next(c for c in m.constraints.values() if c.kind == ConstraintKind.SUBTYPE)
    assert m.object_types[sub.subtype_id].name == "Employee"
    assert m.object_types[sub.supertype_id].name == "Person"

    m = load_example("set_constraints.fg")
    kinds = {c.kind for c in m.constraints.values()}
    assert {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}.issubset(kinds)


def test_v03_subtype_cycle_is_rejected():
    from factgraph.normalize import NormalizeError
    text = '''
model M {
  entity A { }
  entity B { }
  subtype A is B
  subtype B is A
}
'''
    try:
        normalize_model(parse_model(text))
    except NormalizeError as exc:
        assert "cycle" in str(exc).lower()
    else:
        raise AssertionError("expected subtype cycle to be rejected")


def test_v03_subtype_inherits_identity_and_cannot_redeclare_it():
    from factgraph.normalize import NormalizeError
    text = '''
model M {
  value Id: String
  entity Person { id id: Id }
  entity Employee { id employeeId: Id }
  subtype Employee is Person
}
'''
    try:
        normalize_model(parse_model(text))
    except NormalizeError as exc:
        assert "inherits identity" in str(exc).lower()
    else:
        raise AssertionError("expected subtype identifier redeclaration to be rejected")


def test_v03_oneof_literals_must_match_scalar_kind_and_be_unique():
    from factgraph.normalize import NormalizeError
    bad_type = '''
model M {
  value Score: Int { oneof(1, "two") }
}
'''
    duplicate = '''
model M {
  value State: String { oneof("a", "a") }
}
'''
    for text in [bad_type, duplicate]:
        try:
            normalize_model(parse_model(text))
        except NormalizeError:
            pass
        else:
            raise AssertionError("expected invalid oneof declaration to be rejected")


def test_v03_date_timestamp_uuid_oneof_literals_are_lexically_validated():
    from factgraph.normalize import NormalizeError
    bad = [
        'model M { value D: Date { oneof("not-a-date") } }',
        'model M { value T: Timestamp { oneof("not-a-timestamp") } }',
        'model M { value U: UUID { oneof("not-a-uuid") } }',
    ]
    for text in bad:
        try:
            normalize_model(parse_model(text))
        except NormalizeError:
            pass
        else:
            raise AssertionError("expected lexical value literal validation to fail")


def test_v03_range_requires_numeric_value_type():
    from factgraph.normalize import NormalizeError
    text = 'model M { value Label: String { range(0, 10) } }'
    try:
        normalize_model(parse_model(text))
    except NormalizeError as exc:
        assert "range" in str(exc).lower()
    else:
        raise AssertionError("expected non-numeric range to be rejected")


def test_v03_set_constraint_rejects_incompatible_role_players():
    from factgraph.normalize import NormalizeError
    text = '''
model M {
  entity A { }
  entity B { }
  fact F(a: A) { }
  fact G(b: B) { }
  subset F(a) G(b)
}
'''
    try:
        normalize_model(parse_model(text))
    except NormalizeError as exc:
        assert "incompatible" in str(exc).lower()
    else:
        raise AssertionError("expected incompatible role sequence types to be rejected")
