from pathlib import Path

from factgraph.cli import main
from factgraph.importers.linkml import import_linkml
from factgraph.model import ConstraintKind, EntityType

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "external" / "linkml_people.yaml"


def test_linkml_import_preserves_classes_fields_enum_inheritance_and_relationship_cardinality():
    result = import_linkml(FIXTURE.read_text())
    m = result.model

    person = m.object_type_by_name("Person")
    assert isinstance(person, EntityType)
    assert person.id.startswith("uid:entity:linkml%3Aclass%3APerson")

    status_value = m.object_type_by_name("EmploymentStatus")
    assert any(
        c.kind == ConstraintKind.VALUE and c.object_type_id == status_value.id and c.value_spec and c.value_spec.get("values")
        for c in m.constraints.values()
    )

    employer = m.fact_by_name("Person_employer")
    assert employer is not None
    assert any(c.kind == ConstraintKind.MANDATORY and c.fact_type_id == employer.id for c in m.constraints.values())
    assert any(c.kind == ConstraintKind.UNIQUENESS and c.fact_type_id == employer.id for c in m.constraints.values())

    aliases = m.fact_by_name("Person_aliases")
    assert aliases is not None
    assert any(
        c.kind == ConstraintKind.FREQUENCY and c.fact_type_id == aliases.id and c.max_frequency == 3
        for c in m.constraints.values()
    )
    assert any(c.kind == ConstraintKind.SUBTYPE for c in m.constraints.values())
    assert result.report["status"] == "imported"


def test_linkml_import_reports_rules_and_non_identifier_unique_without_guessing():
    text = '''
name: Gapped
slots:
  code:
    range: string
    unique: true
classes:
  Item:
    slots: [code]
    rules:
      - preconditions: {}
'''
    result = import_linkml(text)
    assert result.report["status"] == "imported_with_gaps"
    paths = {i["path"] for i in result.report["issues"] if i["status"] == "unsupported"}
    assert "classes.Item.slots.code.unique" in paths
    assert "classes.Item.rules" in paths


def test_linkml_cli_import_and_auto_audit_write_provenance_and_gaps(tmp_path):
    imported = tmp_path / "imported"
    rc = main(["import-linkml", str(FIXTURE), "--out-dir", str(imported)])
    assert rc == 0
    for rel in ("normalized.fg", "semantic.json", "manifest.json", "import_report.json", "validation.json"):
        assert (imported / rel).is_file(), rel

    audited = tmp_path / "audit"
    rc = main(["audit", str(FIXTURE), "--out-dir", str(audited)])
    assert rc == 0
    report = __import__("json").loads((audited / "source_import" / "import_report.json").read_text())
    assert report["source_format"] == "linkml-schema"
    assert (audited / "source_import" / "source.txt").read_text() == FIXTURE.read_text()
    summary = __import__("json").loads((audited / "AUDIT_SUMMARY.json").read_text())
    assert set(summary["targets"]) == {"postgres", "mongo", "typedb"}
