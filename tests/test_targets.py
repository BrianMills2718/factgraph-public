from pathlib import Path
import json

from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.targets import mongo, graphql
from factgraph.reporting import CapabilityStatus

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text()))


def test_mongo_is_deterministic_and_emits_collection_specs():
    m = load("employment.fg")
    a = mongo.emit_spec_json(m)
    b = mongo.emit_spec_json(m)
    assert a == b
    spec = json.loads(a)
    names = {c["name"] for c in spec["collections"]}
    assert {"person", "company", "employment"}.issubset(names)
    assert all("conceptual_kind" not in c and "source_name" not in c for c in spec["collections"])
    script = mongo.emit_script(m)
    assert 'db.createCollection("employment"' in script
    employment = next(c for c in spec["collections"] if c["name"] == "employment")
    assert employment["validator"]["$jsonSchema"]["properties"]["_id"]["bsonType"] == "objectId"


def test_mongo_unordered_uses_expr_for_simple_refs():
    m = load("role_semantics.fg")
    spec = json.loads(mongo.emit_spec_json(m))
    partnership = next(c for c in spec["collections"] if c["name"] == "partnership")
    assert "$expr" in partnership["validator"]


def test_graphql_is_api_projection_and_keeps_roles():
    m = load("recommendation.fg")
    sdl = graphql.emit_sdl(m)
    assert "type Recommendation" in sdl
    assert "recommender: Person!" in sdl
    assert "candidate: Person!" in sdl
    report = graphql.capability_report(m)
    assert any(e.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED for e in report.entries)


def test_graphql_objectification_is_a_real_referencable_type():
    m = load("employment.fg")
    sdl = graphql.emit_sdl(m)
    assert "type EmploymentRecord" in sdl
    assert "employment: EmploymentRecord!" in sdl
    assert "scalar Decimal" in sdl and "scalar Date" in sdl and "scalar UUID" in sdl


def test_mongo_compound_reference_is_flattened_deterministically():
    m = load("compound_identifier.fg")
    spec = json.loads(mongo.emit_spec_json(m))
    assignment = next(c for c in spec["collections"] if c["name"] == "assignment")
    props = assignment["validator"]["$jsonSchema"]["properties"]
    assert "worker__first_name" in props and "worker__last_name" in props
    assert "worker" not in props


def test_postgres_slug_collision_fails_instead_of_silently_overwriting():
    from factgraph.targets import postgres
    text = '''
model Collision {
  value Id: String
  entity UserID { id id: Id }
  entity UserId { id id: Id }
}
'''
    m = normalize_model(parse_model(text))
    try:
        postgres.emit_sql(m)
    except ValueError as exc:
        assert "collision" in str(exc).lower()
    else:
        raise AssertionError("expected target identifier collision")


def test_v03_postgres_value_constraints_frequency_and_subtyping():
    from factgraph.targets import postgres
    m = load("richer_constraints.fg")
    sql = postgres.emit_sql(m)
    assert "CHECK (age >= 0 AND age <= 1000)" in sql
    assert "CHECK (status IN ('a001', 'm002', 'z003'))" in sql
    assert "ALTER TABLE employee ADD FOREIGN KEY (person_id) REFERENCES person (person_id);" in sql
    membership = next(t for t in postgres.build_plan(m).tables if t.name == "membership")
    assert ("member_id",) in membership.uniques


def test_v03_mongo_value_constraints_and_inherited_subtype_identifier_shape():
    m = load("richer_constraints.fg")
    spec = json.loads(mongo.emit_spec_json(m))
    person = next(c for c in spec["collections"] if c["name"] == "person")
    age = person["validator"]["$jsonSchema"]["properties"]["age"]
    status = person["validator"]["$jsonSchema"]["properties"]["status"]
    assert age["minimum"] == 0 and age["maximum"] == 1000
    assert status["enum"] == ["a001", "m002", "z003"]
    employee = next(c for c in spec["collections"] if c["name"] == "employee")
    assert "person_id" in employee["validator"]["$jsonSchema"]["properties"]
    assert "person_id" in employee["validator"]["$jsonSchema"]["required"]


def test_v03_graphql_marks_subtype_as_metadata_not_database_enforcement():
    m = load("richer_constraints.fg")
    sdl = graphql.emit_sdl(m)
    assert "Semantic subtype of Person" in sdl
    report = graphql.capability_report(m)
    assert any(e.feature == "subtype" and e.status == CapabilityStatus.METADATA_ONLY for e in report.entries)


def test_v03_mongo_does_not_overclaim_bson_native_value_literals():
    text = '''
model M {
  value Amount: Decimal { range(0, 10) }
  value Day: Date { oneof("2026-09-04") }
  entity E { amount: Amount day: Day }
}
'''
    m = normalize_model(parse_model(text))
    spec = json.loads(mongo.emit_spec_json(m))
    coll = next(c for c in spec["collections"] if c["name"] == "e")
    assert "minimum" not in coll["validator"]["$jsonSchema"]["properties"]["amount"]
    assert "enum" not in coll["validator"]["$jsonSchema"]["properties"]["day"]
    report = mongo.capability_report(m)
    values = [e for e in report.entries if e.feature == "value"]
    assert len(values) == 2
    assert all(e.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED for e in values)


def test_bson_value_gap_example_is_explicit_across_targets():
    from factgraph.targets import postgres
    m = load("bson_value_gap.fg")
    sql = postgres.emit_sql(m)
    assert "CHECK (amount >= 0 AND amount <= 1000)" in sql
    assert "CHECK (business_day IN (DATE '2026-09-04', DATE '2026-09-05'))" in sql
    spec = json.loads(mongo.emit_spec_json(m))
    entry = next(c for c in spec["collections"] if c["name"] == "ledger_entry")
    props = entry["validator"]["$jsonSchema"]["properties"]
    assert "minimum" not in props["amount"]
    assert "enum" not in props["business_day"]
    mreport = mongo.capability_report(m)
    assert all(e.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED for e in mreport.entries if e.feature == "value")
