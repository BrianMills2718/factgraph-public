from pathlib import Path

from factgraph.normalize import normalize_model
from factgraph.parser import parse_model
from factgraph.targets import postgres

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text()))


def test_warehouse_postgres_contains_ternary_fact_table():
    m = load("warehouse.fg")
    sql = postgres.emit_sql(m)
    assert "CREATE TABLE stocking" in sql
    assert "part_id" in sql and "bin_id" in sql and "warehouse_id" in sql
    assert "PRIMARY KEY (part_id, bin_id, warehouse_id)" in sql
    assert "ALTER TABLE stocking ADD FOREIGN KEY (part_id) REFERENCES part (part_no);" in sql


def test_objectified_fact_has_identity_and_relationship_fields():
    m = load("employment.fg")
    sql = postgres.emit_sql(m)
    assert "CREATE TABLE employment" in sql
    assert "id BIGINT GENERATED ALWAYS AS IDENTITY" in sql
    assert "salary NUMERIC NOT NULL" in sql
    assert "start_date DATE NOT NULL" in sql
    assert "UNIQUE (employee_id, employer_id)" in sql


def test_compound_identifier_expands_foreign_key_columns():
    m = load("compound_identifier.fg")
    sql = postgres.emit_sql(m)
    assert "PRIMARY KEY (first_name, last_name)" in sql
    assert "worker__first_name" in sql and "worker__last_name" in sql
    assert "ALTER TABLE assignment ADD FOREIGN KEY (worker__first_name, worker__last_name) REFERENCES employee (first_name, last_name);" in sql


def test_unordered_is_check_but_symmetric_is_not_miscompiled_as_unordered():
    m = load("role_semantics.fg")
    sql = postgres.emit_sql(m)
    partnership_block = sql.split("CREATE TABLE partnership", 1)[1].split(");", 1)[0]
    knows_block = sql.split("CREATE TABLE knows", 1)[1].split(");", 1)[0]
    assert "CHECK (a_id <= b_id)" in partnership_block
    assert "CHECK" not in knows_block


def test_postgres_manifest_roundtrip_is_exact_but_structural_reader_admits_losses():
    m = load("employment.fg")
    sql = postgres.emit_sql(m)
    recovered = postgres.recover_with_manifest(sql, m.manifest_json())
    assert m.semantically_equal(recovered)
    structural = postgres.structural_recovery(sql)
    assert structural["tables"]
    assert structural["not_reliably_recoverable_without_metadata"]
