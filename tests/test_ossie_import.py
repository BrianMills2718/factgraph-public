from pathlib import Path

from factgraph.importers.ossie import import_ossie
from factgraph.model import ConstraintKind, EntityType

ROOT = Path(__file__).resolve().parents[1]


def test_ossie_import_preserves_simple_fact_roles_reading_identifier_and_subtype():
    result = import_ossie((ROOT / "examples" / "external" / "ossie_people.yaml").read_text())
    m = result.model
    person = m.object_type_by_name("Person")
    assert isinstance(person, EntityType)
    assert person.id.startswith("uid:entity:ossie%3Aconcept%3APerson")
    earns = m.fact_by_name("Person__earns")
    assert [r.name for r in earns.roles] == ["person", "salary"]
    assert any(r.fact_type_id == earns.id and "earns" in r.template for r in m.readings.values())
    assert any(c.kind == ConstraintKind.UNIQUENESS and c.fact_type_id == earns.id for c in m.constraints.values())
    assert any(c.kind == ConstraintKind.PREFERRED_IDENTIFIER and c.object_type_id == person.id for c in m.constraints.values())
    assert any(c.kind == ConstraintKind.SUBTYPE for c in m.constraints.values())
    assert result.report["status"] == "imported"


def test_ossie_import_reports_unhandled_requires_instead_of_silently_dropping():
    text = '''
name: R
ontology:
  - concept: Code
    type: ValueType
    extends: [String]
    requires: ["Code != ''"]
  - concept: Item
    type: EntityType
    relationships:
      - name: code
        roles: [{concept: Code}]
        verbalizes: ["{Item} has {Code}"]
'''
    result = import_ossie(text)
    assert result.report["status"] == "imported_with_gaps"
    issue = next(i for i in result.report["issues"] if i["path"] == "concept:Code.requires")
    assert issue["status"] == "unsupported"
