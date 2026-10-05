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


RESERVED_NAME_MODEL = """
model SocialNetwork {
  value UserId: UUID
  value GroupId: UUID
  value Rank: Int

  entity User {
    id userId: UserId
    order: Rank
  }

  entity Group {
    id groupId: GroupId
  }

  fact Conversation(a: User, b: User, group: Group) {
    reading "{a} and {b} talked inside {group}"
    unordered(a, b)
  }
}
"""


def test_reserved_entity_and_field_names_get_a_trailing_underscore():
    # `user`, `group` and `order` are words PostgreSQL refuses as unquoted names; before pg_name the
    # generated file began `CREATE TABLE group (` and did not parse.
    m = normalize_model(parse_model(RESERVED_NAME_MODEL))
    sql = postgres.emit_sql(m)
    assert "CREATE TABLE user_ (" in sql and "CREATE TABLE group_ (" in sql
    assert "CREATE TABLE user (" not in sql and "CREATE TABLE group (" not in sql
    assert "order_ BIGINT NOT NULL" in sql or "order_ INTEGER NOT NULL" in sql
    assert "REFERENCES user_ (" in sql and "REFERENCES group_ (" in sql
    # ordinary names are unchanged
    assert "CREATE TABLE conversation (" in sql and "group_id UUID NOT NULL" in sql
    conversation = sql[sql.index("CREATE TABLE conversation ("):]
    conversation = conversation[: conversation.index(");")]
    assert "group_id UUID NOT NULL" in conversation and "group__id" not in conversation
    assert postgres.pg_name("Conversation") == "conversation" and postgres.pg_name("User") == "user_"


def test_conformance_and_witness_sql_use_the_same_reserved_safe_names():
    from factgraph import conformance
    m = normalize_model(parse_model(RESERVED_NAME_MODEL))
    statements = [s["sql"] for c in conformance.postgres_cases(m) for s in [*c.steps] if "sql" in s]
    assert statements, "expected PostgreSQL conformance statements"
    assert not any(s.startswith(("INSERT INTO user ", "INSERT INTO group ")) for s in statements)
    assert any(s.startswith("INSERT INTO user_ ") for s in statements)


def test_a_bare_value_role_named_after_a_reserved_word_is_renamed():
    m = normalize_model(parse_model("""
model Ranking {
  value Position: Int
  value PlayerId: UUID
  entity Player {
    id playerId: PlayerId
  }
  fact Ranked(player: Player, order: Position) {
    reading "{player} is ranked {order}"
  }
}
"""))
    sql = postgres.emit_sql(m)
    assert "order_ BIGINT NOT NULL" in sql or "order_ INTEGER NOT NULL" in sql
    assert "player_id UUID NOT NULL" in sql
