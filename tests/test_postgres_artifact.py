from __future__ import annotations

import json
from pathlib import Path

from factgraph.cli import load
from factgraph.postgres_artifact import (
    _refine_observation,
    build_external_acceptance_cases,
    build_external_cases,
    mapping_template,
    validate_mapping,
    write_bundle,
)
from factgraph.targets import postgres

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "examples" / "portability"


def _renamed_value_range():
    model = load(PORT / "value_range.fg")
    sql = """CREATE TABLE people_record (\n    pid TEXT NOT NULL,\n    years INTEGER NOT NULL,\n    PRIMARY KEY (pid)\n);\n"""
    mapping = mapping_template(model, sql)
    person = mapping["entities"]["entity:Person"]
    person["table"] = "people_record"
    person["field_columns"]["fact:Person__personId"] = "pid"
    person["field_columns"]["fact:Person__age"] = "years"
    return model, sql, mapping


def test_mapping_template_validates_canonical_postgres_projection():
    model = load(PORT / "value_range.fg")
    sql = postgres.emit_sql(model)
    mapping = mapping_template(model, sql)
    validation = validate_mapping(model, sql, mapping)
    assert validation["passed"] is True


def test_external_mapping_rewrites_same_semantic_value_witness_to_renamed_physical_layout():
    model, sql, mapping = _renamed_value_range()
    assert validate_mapping(model, sql, mapping)["passed"] is True
    cases = build_external_cases(model, mapping)
    value_case = next(c for c in cases if c.kind == "value")
    assert value_case.target == "postgres_external"
    assert value_case.expected_write_outcome == "not_asserted"
    assert value_case.program.status == "lowered"
    assert [op["sql"] for op in value_case.program.operations] == [
        "INSERT INTO people_record (pid, years) VALUES ('v2', -1);"
    ]


def test_external_mapping_rewrites_source_valid_value_boundaries_to_same_physical_layout():
    model, sql, mapping = _renamed_value_range()
    probes = [p for p in build_external_acceptance_cases(model, mapping) if p.kind == "value"]
    assert [p.label for p in probes] == ["source lower boundary 0", "source upper boundary 130"]
    assert [[op["sql"] for op in p.program.operations] for p in probes] == [
        ["INSERT INTO people_record (pid, years) VALUES ('v2', 0);"],
        ["INSERT INTO people_record (pid, years) VALUES ('v2', 130);"],
    ]


def test_acceptance_probes_refine_preserved_or_stronger_without_overclaiming_equivalence():
    accepted = [
        {"status": "observed", "accepted": True},
        {"status": "observed", "accepted": True},
    ]
    assert _refine_observation("preserved_or_stronger", accepted) == "preserved_on_tested_cases"
    assert _refine_observation("preserved_or_stronger", [accepted[0], {"status": "observed", "accepted": False}]) == "stronger_or_incompatible"
    assert _refine_observation("weakened", accepted) == "weakened"


def test_external_mapping_is_bound_to_exact_artifact_bytes():
    model, sql, mapping = _renamed_value_range()
    validation = validate_mapping(model, sql + "\n-- changed\n", mapping)
    assert validation["passed"] is False
    assert any("artifact_sha256" in e for e in validation["errors"])


def test_mapping_v2_supports_exact_case_sensitive_postgres_identifiers():
    model = load(PORT / "value_range.fg")
    sql = 'CREATE TABLE "Person" ("personId" TEXT NOT NULL, "age" INTEGER NOT NULL, PRIMARY KEY ("personId"));\n'
    mapping = mapping_template(model, sql)
    person = mapping["entities"]["entity:Person"]
    person["table"] = "Person"
    person["field_columns"]["fact:Person__personId"] = "personId"
    person["field_columns"]["fact:Person__age"] = "age"
    assert validate_mapping(model, sql, mapping)["passed"] is True
    case = next(c for c in build_external_cases(model, mapping) if c.kind == "value")
    assert case.program.operations[0]["sql"] == 'INSERT INTO "Person" ("personId", age) VALUES (\'v2\', -1);'


def test_invalid_mapping_writes_non_executable_report_instead_of_crashing(tmp_path):
    model, sql, mapping = _renamed_value_range()
    del mapping["entities"]["entity:Person"]["field_columns"]["fact:Person__age"]
    report = write_bundle(model, sql, mapping, tmp_path)
    assert report["status"] == "invalid_mapping"
    assert report["case_count"] == 0
    assert report["case_plan_sha256"] is None
    assert json.loads((tmp_path / "mapping_validation.json").read_text())["passed"] is False
    assert (tmp_path / "report.md").is_file()


def test_generated_external_bundle_is_explicitly_not_live(tmp_path):
    model, sql, mapping = _renamed_value_range()
    report = write_bundle(model, sql, mapping, tmp_path)
    assert report["status"] == "generated_not_run"
    assert report["case_count"] > 0
    assert report["acceptance_probe_count"] >= 5
    assert report["acceptance_lowering_counts"].get("lowered", 0) >= 5
    assert report["results"] == []
    assert report["acceptance_results"] == []
    index = json.loads((tmp_path / "witnesses" / "index.json").read_text())
    assert any(row["lowering_status"] == "lowered" for row in index)
    acceptance = json.loads((tmp_path / "acceptance_probes" / "index.json").read_text())
    assert len(acceptance) == report["acceptance_probe_count"]
    assert {row["kind"] for row in acceptance} >= {"value", "preferred_identifier", "mandatory", "uniqueness"}


def test_external_total_participation_positive_probe_lowers_to_actual_relationship_insert():
    model = load(PORT / "total_participation.fg")
    sql = postgres.emit_sql(model)
    mapping = mapping_template(model, sql)
    probes = [p for p in build_external_acceptance_cases(model, mapping) if p.kind == "mandatory"]
    assert len(probes) == 1
    probe = probes[0]
    assert probe.label == "source-valid participation in Employment.employee"
    sql_ops = [op["sql"] for op in probe.program.operations]
    assert any(stmt.startswith("INSERT INTO employment") for stmt in sql_ops)
    assert any(stmt.startswith("INSERT INTO person") for stmt in sql_ops)


def test_external_postgres_cli_template_and_audit_are_file_oriented(tmp_path):
    from factgraph.cli import main

    model_path = PORT / "value_range.fg"
    schema = tmp_path / "external.sql"
    schema.write_text("""CREATE TABLE people_record (\n    pid TEXT NOT NULL,\n    years INTEGER NOT NULL,\n    PRIMARY KEY (pid)\n);\n""", encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    assert main([
        "postgres-map-template", str(model_path), "--schema", str(schema), "--out", str(mapping_path)
    ]) == 0
    mapping = json.loads(mapping_path.read_text())
    row = mapping["entities"]["entity:Person"]
    row["table"] = "people_record"
    row["field_columns"]["fact:Person__personId"] = "pid"
    row["field_columns"]["fact:Person__age"] = "years"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    out = tmp_path / "audit"
    assert main([
        "audit-postgres-artifact", str(model_path), "--schema", str(schema),
        "--mapping", str(mapping_path), "--out-dir", str(out),
    ]) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "generated_not_run"
    value = next(c for c in report["cases"] if c["kind"] == "value")
    assert "people_record (pid, years)" in value["program"]["operations"][0]["sql"]
    assert (out / "artifact.sql").read_text() == schema.read_text()


def _upstream_factum_absorbed_case():
    from factgraph.importers.factum import import_factum
    from factgraph.model import EntityType

    source = {
        "version": 2, "name": "AbsorbedFunctionalFixture",
        "objectTypes": [
            {"id": "person", "name": "Person", "kind": "entity", "refMode": "nr"},
            {"id": "company", "name": "Company", "kind": "entity", "refMode": "name"},
            {"id": "nick", "name": "Nickname", "kind": "value", "dataType": "string"},
        ],
        "factTypes": [
            {"id": "works", "roles": [{"id": "works.r0", "objectTypeId": "person"}, {"id": "works.r1", "objectTypeId": "company"}],
             "readings": [{"id": "works.rd", "roleOrder": ["works.r0", "works.r1"], "text": "{0} works for {1}", "isPrimary": True}]},
            {"id": "nickname", "roles": [{"id": "nickname.r0", "objectTypeId": "person"}, {"id": "nickname.r1", "objectTypeId": "nick"}],
             "readings": [{"id": "nickname.rd", "roleOrder": ["nickname.r0", "nickname.r1"], "text": "{0} is called {1}", "isPrimary": True}]},
        ],
        "subtypeRelations": [],
        "constraints": [
            {"id": "uc1", "kind": "uniqueness", "roles": ["works.r0"]},
            {"id": "mc1", "kind": "mandatory", "roles": ["works.r0"]},
            {"id": "uc2", "kind": "uniqueness", "roles": ["nickname.r0"]},
        ],
    }
    imported = import_factum(json.dumps(source))
    model = imported.model
    assert imported.report["unsupported_or_lossy_count"] == 0
    sql = '''CREATE TABLE "Company" ("companyName" varchar(255) NOT NULL, CONSTRAINT "PK_Company" PRIMARY KEY ("companyName"));
CREATE TABLE "Person" ("personNr" integer NOT NULL, "companyName" varchar(255) NOT NULL, "nickname" varchar(255), CONSTRAINT "PK_Person" PRIMARY KEY ("personNr"), CONSTRAINT "FK_Person_Company" FOREIGN KEY ("companyName") REFERENCES "Company" ("companyName"));
'''
    mapping = mapping_template(model, sql)
    entities = {o.name: o for o in model.object_types.values() if isinstance(o, EntityType)}
    person = entities["Person"]
    company = entities["Company"]
    mapping["entities"][person.id]["table"] = "Person"
    mapping["entities"][company.id]["table"] = "Company"
    for hint in model.field_hints.values():
        if hint.owner_object_type_id == person.id:
            mapping["entities"][person.id]["field_columns"][hint.field_fact_id] = "personNr"
        elif hint.owner_object_type_id == company.id:
            mapping["entities"][company.id]["field_columns"][hint.field_fact_id] = "companyName"
    source_facts = {f.name: f for f in model.fact_types.values() if f.id not in model.field_hints}
    works = source_facts["works"]
    nickname = source_facts["nickname"]
    for fact, non_anchor_column in ((works, "companyName"), (nickname, "nickname")):
        row = mapping["facts"][fact.id]
        row["storage_mode"] = "absorbed"
        anchor = next(r for r in fact.roles if r.player_id == person.id)
        other = next(r for r in fact.roles if r.id != anchor.id)
        row["anchor_role_id"] = anchor.id
        row["table"] = "Person"
        row["role_columns"][anchor.id] = ["personNr"]
        row["role_columns"][other.id] = [non_anchor_column]
    return model, sql, mapping, works, nickname


def test_mapping_v3_accepts_bounded_absorbed_binary_facts_on_anchor_entity_table():
    model, sql, mapping, _works, _nickname = _upstream_factum_absorbed_case()
    result = validate_mapping(model, sql, mapping)
    assert result["passed"] is True, result["errors"]
    assert result["format"] == "factgraph-postgres-artifact-mapping-validation-v3"


def test_mapping_v2_still_rejects_shared_entity_fact_table():
    model, sql, mapping, works, _nickname = _upstream_factum_absorbed_case()
    mapping["format"] = "factgraph-postgres-artifact-mapping-v2"
    mapping["facts"][works.id]["storage_mode"] = "table"
    mapping["facts"][works.id]["anchor_role_id"] = None
    result = validate_mapping(model, sql, mapping)
    assert result["passed"] is False
    assert any("share entity table" in e or "shared" in e for e in result["errors"])


def test_absorbed_mandatory_negative_witness_remains_anchor_row_without_relationship_columns():
    model, _sql, mapping, works, _nickname = _upstream_factum_absorbed_case()
    target = next(
        c for c in build_external_cases(model, mapping)
        if c.kind == "mandatory" and model.constraints[c.obligation_id].fact_type_id == works.id
    )
    assert target.program.status == "lowered"
    assert [op["sql"] for op in target.program.operations] == [
        'INSERT INTO "Person" ("personNr") VALUES (101);'
    ]
    assert target.poststate_required_for_semantic_proof is True
    assert len(target.postconditions) == 1
    assert '"companyName" IS NOT NULL' in target.postconditions[0]["sql"]
    assert target.postconditions[0]["physical_semantics"] == "absorbed_fact_presence_via_non_anchor_nonnull_columns"


def test_absorbed_mandatory_positive_probe_coalesces_relationship_into_anchor_row():
    model, _sql, mapping, works, _nickname = _upstream_factum_absorbed_case()
    target = next(
        c for c in build_external_acceptance_cases(model, mapping)
        if c.kind == "mandatory" and model.constraints[c.obligation_id].fact_type_id == works.id
    )
    statements = [op["sql"] for op in target.program.operations]
    assert statements == [
        'INSERT INTO "Company" ("companyName") VALUES (\'v13\');',
        'INSERT INTO "Person" ("personNr", "companyName") VALUES (101, \'v13\');',
    ]


def test_required_physical_columns_union_entity_and_absorbed_fact_columns():
    from factgraph.postgres_artifact import _required_physical_columns
    model, _sql, mapping, _works, _nickname = _upstream_factum_absorbed_case()
    required = _required_physical_columns(model, mapping)
    assert required["Person"] >= {"personNr", "companyName", "nickname"}


def test_duplicate_absorbed_occurrence_is_not_silently_deduplicated():
    from factgraph.population import SemanticPopulation
    from factgraph import witness_lowering
    model, _sql, mapping, works, _nickname = _upstream_factum_absorbed_case()
    person_role, company_role = sorted(works.roles, key=lambda r: r.ordinal)
    person = model.object_types[person_role.player_id]
    company = model.object_types[company_role.player_id]
    pop = SemanticPopulation()
    p, c1, c2 = "p", "c1", "c2"
    pop.add_membership(person.id, p)
    pop.add_membership(company.id, c1)
    pop.add_membership(company.id, c2)
    # Give the three entities their source identifiers through the field facts.
    for entity, iid, literal in ((person, p, "P1"), (company, c1, "C1"), (company, c2, "C2")):
        hint = next(h for h in model.field_hints.values() if h.owner_object_type_id == entity.id and h.identifier_component)
        vi = f"{iid}:key"
        pop.add_value(hint.value_type_id, vi, literal)
        pop.add_fact(hint.field_fact_id, (iid, vi))
    pop.add_fact(works.id, (p, c1))
    pop.add_fact(works.id, (p, c2))
    canonical = witness_lowering.lower_population(model, pop, "postgres")
    from factgraph.postgres_artifact import _rewrite_program
    external = _rewrite_program(model, canonical, mapping)
    person_inserts = [op["sql"] for op in external.operations if 'INSERT INTO "Person"' in op["sql"]]
    assert len(person_inserts) == 2
    assert any("'C1'" in stmt for stmt in person_inserts)
    assert any("'C2'" in stmt for stmt in person_inserts)


def test_duplicate_absorbed_fact_rows_include_later_mandatory_absorbed_context():
    from factgraph.population import SemanticPopulation
    from factgraph import witness_lowering
    model, _sql, mapping, works, nickname = _upstream_factum_absorbed_case()
    person_role, company_role = sorted(works.roles, key=lambda r: r.ordinal)
    person = model.object_types[person_role.player_id]
    company = model.object_types[company_role.player_id]
    nick_role = next(r for r in nickname.roles if r.player_id != person.id)
    nick_type = model.object_types[nick_role.player_id]
    pop = SemanticPopulation()
    p, c = "p", "c"
    pop.add_membership(person.id, p)
    pop.add_membership(company.id, c)
    for entity, iid, literal in ((person, p, "P1"), (company, c, "C1")):
        hint = next(h for h in model.field_hints.values() if h.owner_object_type_id == entity.id and h.identifier_component)
        vi = f"{iid}:key"
        pop.add_value(hint.value_type_id, vi, literal)
        pop.add_fact(hint.field_fact_id, (iid, vi))
    n1, n2 = "n1", "n2"
    pop.add_value(nick_type.id, n1, "Alpha")
    pop.add_value(nick_type.id, n2, "Beta")
    # nickname sorts before works in this imported model, so this specifically
    # guards against the duplicate row being emitted before mandatory works
    # context has been merged into the Person row.
    pop.add_fact(nickname.id, (p, n1))
    pop.add_fact(nickname.id, (p, n2))
    pop.add_fact(works.id, (p, c))
    canonical = witness_lowering.lower_population(model, pop, "postgres")
    from factgraph.postgres_artifact import _rewrite_program
    external = _rewrite_program(model, canonical, mapping)
    person_inserts = [op["sql"] for op in external.operations if 'INSERT INTO "Person"' in op["sql"]]
    assert len(person_inserts) == 2
    assert all('"companyName"' in stmt and "'C1'" in stmt for stmt in person_inserts)
    assert any("'Alpha'" in stmt for stmt in person_inserts)
    assert any("'Beta'" in stmt for stmt in person_inserts)
