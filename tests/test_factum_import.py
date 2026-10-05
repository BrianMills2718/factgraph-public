from pathlib import Path
import json

from factgraph.cli import main
from factgraph.importers.factum import import_factum
from factgraph.model import ConstraintKind, ObjectifiedFactType
from factgraph.validate import validate_model

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "external" / "factum_people.orm.json"


def test_factum_import_preserves_identity_refmode_objectification_constraints_and_samples():
    result = import_factum(FIXTURE.read_text())
    model = result.model

    person = model.object_type_by_name("Person")
    assert person.id.startswith("uid:entity:factum%3Aguid%3A11111111")
    employment_obj = model.object_type_by_name("Employment")
    assert isinstance(employment_obj, ObjectifiedFactType)

    employment = model.fact_by_name("ft_employment")
    assert [r.name for r in employment.roles] == ["employee", "employer"]
    assert model.objectification_for_fact(employment.id).id == employment_obj.id
    assert any(c.kind == ConstraintKind.UNIQUENESS and c.fact_type_id == employment.id for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.MANDATORY and c.fact_type_id == employment.id for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.FREQUENCY and c.fact_type_id == employment.id and c.max_frequency == 10 for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.SUBSET for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.SUBTYPE for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.RING and c.ring_kind == "symmetric" for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.VALUE for c in model.constraints.values())
    assert any(c.kind == ConstraintKind.PREFERRED_IDENTIFIER for c in model.constraints.values())
    assert len(model.samples) == 2
    assert not [d for d in validate_model(model) if d.severity.value == "error"]
    assert result.report["status"] == "imported"
    assert result.report["unsupported_or_lossy_count"] == 0


def test_factum_import_does_not_strengthen_deontic_disjunctive_or_role_scoped_semantics():
    raw = {
        "version": 2,
        "name": "GappedFactum",
        "objectTypes": [
            {"id": "person", "name": "Person", "kind": "entity", "refMode": "id", "dataType": "integer"},
            {"id": "code", "name": "Code", "kind": "value", "dataType": "string"},
        ],
        "factTypes": [
            {
                "id": "has_code",
                "roles": [
                    {"id": "rp", "objectTypeId": "person"},
                    {"id": "rc", "objectTypeId": "code"},
                ],
                "readings": [{"id": "read", "roleOrder": ["rp", "rc"], "text": "{0} has {1}"}],
            }
        ],
        "constraints": [
            {"id": "d1", "kind": "uniqueness", "roles": ["rp"], "modality": "deontic"},
            {"id": "m1", "kind": "mandatory", "roles": ["rp", "rc"]},
            {"id": "v1", "kind": "value", "roleId": "rc", "ranges": [{"value": "A"}]},
            {"id": "r1", "kind": "ring", "roles": ["rp", "rc"], "types": ["acyclic"]},
        ],
    }
    result = import_factum(json.dumps(raw))
    assert result.report["status"] == "imported_with_gaps"
    paths = {i["path"] for i in result.report["issues"] if i["status"] == "unsupported"}
    assert "constraints.d1.modality" in paths
    assert "constraints.m1" in paths
    assert "constraints.v1.roleId" in paths
    assert "constraints.r1.types" in paths
    source = result.generated_source
    assert "unique(person)" not in source
    assert "mandatory(person)" not in source
    assert "oneof(\"A\")" not in source
    assert "symmetric" not in source


def test_factum_cli_import_and_auto_audit_surface_source_semantic_coverage(tmp_path):
    imported = tmp_path / "imported"
    rc = main(["import-factum", str(FIXTURE), "--out-dir", str(imported)])
    assert rc == 0
    for rel in ("normalized.fg", "semantic.json", "manifest.json", "import_report.json", "validation.json"):
        assert (imported / rel).is_file(), rel

    audited = tmp_path / "audit"
    rc = main(["audit", str(FIXTURE), "--out-dir", str(audited)])
    assert rc == 0
    report = json.loads((audited / "source_import" / "import_report.json").read_text())
    assert report["source_format"] == "factum-orm-json"
    summary = json.loads((audited / "AUDIT_SUMMARY.json").read_text())
    assert summary["source_import"]["semantic_input_complete"] is True
    assert summary["source_import"]["unsupported_or_lossy_count"] == 0
    assert summary["semantic_counterexamples"]["counts"] == {
        "collateral": 0,
        "isolated": 17,
        "not_independently_falsifiable": 0,
        "unsupported": 0,
    }
    assert summary["semantic_counterexamples"]["locally_irreducible_count"] == 17
    assert "## Source import" in (audited / "audit.md").read_text()
    assert set(summary["targets"]) == {"postgres", "mongo", "typedb"}


def test_factum_reference_mode_default_datatype_matches_factum_rmap_convention():
    from factgraph.importers.factum import import_factum
    text = json.dumps({
        "version": 2,
        "name": "RefModeDefaults",
        "objectTypes": [
            {"id": "p", "name": "Person", "kind": "entity", "refMode": "nr"},
            {"id": "c", "name": "Company", "kind": "entity", "refMode": "name"},
        ],
        "factTypes": [],
        "subtypeRelations": [],
        "constraints": [],
    })
    result = import_factum(text)
    values = {o.name: o for o in result.model.object_types.values() if hasattr(o, "scalar_kind")}
    assert values["Person_nr_Reference"].scalar_kind == "Int"
    assert values["Company_name_Reference"].scalar_kind == "String"
