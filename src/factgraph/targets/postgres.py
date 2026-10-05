from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
import json
import re

from ..ids import slug
from ..model import ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType
from ..reporting import CapabilityEntry, CapabilityReport, CapabilityStatus


# Words PostgreSQL's grammar rejects as an unquoted table or column name: its "reserved" and
# "type or function name" keyword categories (PostgreSQL 17 grammar, via libpg_query). Each was checked
# by parsing CREATE TABLE, column, INSERT and FOREIGN KEY statements that use it as a name.
PG_RESERVED_NAMES = frozenset({
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "authorization",
    "binary", "both", "case", "cast", "check", "collate", "collation", "column", "concurrently",
    "constraint", "create", "cross", "current_catalog", "current_date", "current_role",
    "current_schema", "current_time", "current_timestamp", "current_user", "default", "deferrable",
    "desc", "distinct", "do", "else", "end", "except", "false", "fetch", "for", "foreign", "freeze",
    "from", "full", "grant", "group", "having", "ilike", "in", "initially", "inner", "intersect",
    "into", "is", "isnull", "join", "lateral", "leading", "left", "like", "limit", "localtime",
    "localtimestamp", "natural", "not", "notnull", "null", "offset", "on", "only", "or", "order",
    "outer", "overlaps", "placing", "primary", "references", "returning", "right", "select",
    "session_user", "similar", "some", "symmetric", "system_user", "table", "tablesample", "then",
    "to", "trailing", "true", "union", "unique", "user", "using", "variadic", "verbose", "when",
    "where", "window", "with"
})


def pg_name(text: str) -> str:
    """A PostgreSQL table or column name for `text`: its slug, with "_" appended if the slug is reserved.

    Without this, a model with an entity called User or Group produces `CREATE TABLE user (...)`, which
    PostgreSQL refuses to parse. Every PostgreSQL name factgraph writes goes through this function.
    """
    name = slug(text)
    return name + "_" if name in PG_RESERVED_NAMES else name


SCALAR_SQL = {
    "String": "TEXT",
    "Int": "INTEGER",
    "Decimal": "NUMERIC",
    "Date": "DATE",
    "UUID": "UUID",
    "Bool": "BOOLEAN",
    "Timestamp": "TIMESTAMPTZ",
    "Float": "DOUBLE PRECISION",
}


@dataclass(frozen=True)
class PgColumn:
    name: str
    sql_type: str
    nullable: bool


@dataclass(frozen=True)
class PgForeignKey:
    columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]


@dataclass(frozen=True)
class PgTable:
    name: str
    columns: tuple[PgColumn, ...]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[PgForeignKey, ...]
    uniques: tuple[tuple[str, ...], ...]
    checks: tuple[str, ...]
    conceptual_kind: str
    source_name: str


@dataclass
class PgPlan:
    tables: list[PgTable]

    def to_dict(self) -> dict:
        return {"tables": [asdict(t) for t in self.tables]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def _value_sql(model: Model, value_type_id: str) -> str:
    obj = model.object_types[value_type_id]
    if not isinstance(obj, ValueType):
        raise TypeError(f"expected ValueType, got {obj}")
    return SCALAR_SQL[obj.scalar_kind]


def _hints_for_owner(model: Model, owner_id: str):
    return sorted(
        (h for h in model.field_hints.values() if h.owner_object_type_id == owner_id),
        key=lambda h: (not h.identifier_component, h.field_name),
    )


def _direct_identifier_hints(model: Model, entity_id: str):
    return [h for h in _hints_for_owner(model, entity_id) if h.identifier_component]


def _identifier_hints(model: Model, entity_id: str):
    direct = _direct_identifier_hints(model, entity_id)
    if direct:
        return direct
    sup = model.supertype_of(entity_id)
    if isinstance(sup, EntityType):
        return _identifier_hints(model, sup.id)
    return []


def _entity_key(model: Model, entity: EntityType) -> tuple[tuple[str, str], ...]:
    ids = _identifier_hints(model, entity.id)
    if ids:
        return tuple((pg_name(h.field_name), _value_sql(model, h.value_type_id)) for h in ids)
    return (("id", "BIGINT"),)


def _objectified_table_for(model: Model, objectified: ObjectifiedFactType) -> str:
    return pg_name(model.fact_types[objectified.fact_type_id].name)


def _role_columns(model: Model, role) -> tuple[tuple[str, str], ...]:
    player = model.object_types[role.player_id]
    # A role column carries a suffix (group -> group_id), so only a bare value-role column can be reserved.
    base = slug(role.name)
    if isinstance(player, ValueType):
        return ((pg_name(role.name), _value_sql(model, player.id)),)
    if isinstance(player, EntityType):
        key = _entity_key(model, player)
        if len(key) == 1:
            return ((f"{base}_id", key[0][1]),)
        return tuple((f"{base}__{col}", typ) for col, typ in key)
    if isinstance(player, ObjectifiedFactType):
        return ((f"{base}_id", "BIGINT"),)
    raise TypeError(player)


def _role_fk(model: Model, role) -> PgForeignKey | None:
    player = model.object_types[role.player_id]
    cols = tuple(c for c, _ in _role_columns(model, role))
    if isinstance(player, EntityType):
        key = _entity_key(model, player)
        return PgForeignKey(cols, pg_name(player.name), tuple(c for c, _ in key))
    if isinstance(player, ObjectifiedFactType):
        return PgForeignKey(cols, _objectified_table_for(model, player), ("id",))
    return None


def _sql_literal(value, scalar_kind: str) -> str:
    if scalar_kind in {"Int", "Decimal", "Float"}:
        return str(value)
    if scalar_kind == "Bool":
        return "TRUE" if bool(value) else "FALSE"
    escaped = str(value).replace("'", "''")
    if scalar_kind == "Date":
        return f"DATE '{escaped}'"
    if scalar_kind == "Timestamp":
        return f"TIMESTAMPTZ '{escaped}'"
    if scalar_kind == "UUID":
        return f"'{escaped}'::uuid"
    return f"'{escaped}'"


def _value_checks_for_column(model: Model, value_type_id: str, column: str) -> list[str]:
    vt = model.object_types[value_type_id]
    assert isinstance(vt, ValueType)
    out: list[str] = []
    for c in model.constraints_for_value(vt.id):
        spec = c.value_spec or {}
        if spec.get("kind") == "range":
            out.append(
                f"{column} >= {_sql_literal(spec['min'], vt.scalar_kind)} AND {column} <= {_sql_literal(spec['max'], vt.scalar_kind)}"
            )
        elif spec.get("kind") == "oneof":
            vals = ", ".join(_sql_literal(v, vt.scalar_kind) for v in spec.get("values", []))
            out.append(f"{column} IN ({vals})")
    return out


def _value_constraint_is_used(model: Model, value_type_id: str) -> bool:
    if any(h.value_type_id == value_type_id for h in model.field_hints.values()):
        return True
    return any(r.player_id == value_type_id for f in model.fact_types.values() if f.id not in model.field_hints for r in f.roles)


def build_plan(model: Model) -> PgPlan:
    tables: list[PgTable] = []

    # Entities become tables. Subtypes use table-per-type: inherited key is both PK and FK to supertype.
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.name):
        columns: list[PgColumn] = []
        foreign_keys: list[PgForeignKey] = []
        checks: list[str] = []
        subtype = model.subtype_constraint(entity.id)
        direct_ids = _direct_identifier_hints(model, entity.id)
        effective_ids = _identifier_hints(model, entity.id)
        if subtype is not None:
            sup = model.object_types[subtype.supertype_id]
            assert isinstance(sup, EntityType)
            key = _entity_key(model, sup)
            inherited_hints = _identifier_hints(model, sup.id)
            if inherited_hints:
                for hint in inherited_hints:
                    name = pg_name(hint.field_name)
                    columns.append(PgColumn(name, _value_sql(model, hint.value_type_id), False))
                    checks.extend(_value_checks_for_column(model, hint.value_type_id, name))
            else:
                columns.append(PgColumn("id", key[0][1], False))
            pk = tuple(c for c, _ in key)
            foreign_keys.append(PgForeignKey(pk, pg_name(sup.name), pk))
        elif direct_ids:
            for hint in direct_ids:
                name = pg_name(hint.field_name)
                columns.append(PgColumn(name, _value_sql(model, hint.value_type_id), False))
                checks.extend(_value_checks_for_column(model, hint.value_type_id, name))
            pk = tuple(pg_name(h.field_name) for h in direct_ids)
        else:
            columns.append(PgColumn("id", "BIGINT GENERATED ALWAYS AS IDENTITY", False))
            pk = ("id",)
        for hint in _hints_for_owner(model, entity.id):
            if hint.identifier_component:
                continue
            name = pg_name(hint.field_name)
            columns.append(PgColumn(name, _value_sql(model, hint.value_type_id), not hint.required))
            checks.extend(_value_checks_for_column(model, hint.value_type_id, name))
        tables.append(PgTable(pg_name(entity.name), tuple(columns), pk, tuple(foreign_keys), (), tuple(checks), "subtype_entity" if subtype else "entity", entity.name))

    # Source fact types become relationship tables. Objectified facts receive identity and carry their own fields.
    source_facts = sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.name)
    for fact in source_facts:
        obj = model.objectification_for_fact(fact.id)
        columns: list[PgColumn] = []
        foreign_keys: list[PgForeignKey] = []
        role_to_cols: dict[str, tuple[str, ...]] = {}
        checks: list[str] = []
        if obj is not None:
            columns.append(PgColumn("id", "BIGINT GENERATED ALWAYS AS IDENTITY", False))
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            rcols = _role_columns(model, role)
            role_to_cols[role.id] = tuple(name for name, _ in rcols)
            columns.extend(PgColumn(name, typ, False) for name, typ in rcols)
            player = model.object_types[role.player_id]
            if isinstance(player, ValueType):
                for name, _ in rcols:
                    checks.extend(_value_checks_for_column(model, player.id, name))
            fk = _role_fk(model, role)
            if fk:
                foreign_keys.append(fk)
        if obj is not None:
            for hint in _hints_for_owner(model, obj.id):
                name = pg_name(hint.field_name)
                columns.append(PgColumn(name, _value_sql(model, hint.value_type_id), not hint.required))
                checks.extend(_value_checks_for_column(model, hint.value_type_id, name))

        full_tuple = tuple(c for role in sorted(fact.roles, key=lambda r: r.ordinal) for c in role_to_cols[role.id])
        uniques: list[tuple[str, ...]] = []
        if obj is not None:
            pk = ("id",)
            if full_tuple:
                uniques.append(full_tuple)
        else:
            pk = full_tuple

        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNIQUENESS):
            cols = tuple(col for rid in c.role_ids for col in role_to_cols[rid])
            if cols and cols != pk and cols not in uniques:
                uniques.append(cols)
        for c in model.constraints_for_fact(fact.id, ConstraintKind.FREQUENCY):
            if c.max_frequency == 1:
                cols = tuple(col for rid in c.role_ids for col in role_to_cols[rid])
                if cols and cols != pk and cols not in uniques:
                    uniques.append(cols)

        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNORDERED_ROLE_GROUP):
            groups = [role_to_cols[rid] for rid in c.role_ids]
            for left, right in zip(groups, groups[1:]):
                if len(left) == 1:
                    checks.append(f"{left[0]} <= {right[0]}")
                else:
                    checks.append(f"({', '.join(left)}) <= ({', '.join(right)})")

        tables.append(PgTable(
            pg_name(fact.name), tuple(columns), pk, tuple(foreign_keys), tuple(uniques), tuple(checks),
            "objectified_fact" if obj is not None else "fact", fact.name
        ))

    names = [t.name for t in tables]
    if len(names) != len(set(names)):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"PostgreSQL target identifier collision after normalization: {duplicates}")
    for table in tables:
        cols = [c.name for c in table.columns]
        if len(cols) != len(set(cols)):
            duplicates = sorted({n for n in cols if cols.count(n) > 1})
            raise ValueError(f"PostgreSQL column collision in {table.name}: {duplicates}")
    return PgPlan(tables)


def emit_sql(model: Model) -> str:
    plan = build_plan(model)
    lines = [
        "-- generated by factgraph; deterministic canonical PostgreSQL projection",
        "-- pure target artifact: semantic recovery metadata is stored separately",
        "",
    ]
    for table in plan.tables:
        lines.append(f"CREATE TABLE {table.name} (")
        defs: list[str] = []
        for col in table.columns:
            null = "" if col.nullable else " NOT NULL"
            defs.append(f"    {col.name} {col.sql_type}{null}")
        if table.primary_key:
            defs.append(f"    PRIMARY KEY ({', '.join(table.primary_key)})")
        for u in table.uniques:
            defs.append(f"    UNIQUE ({', '.join(u)})")
        for check in table.checks:
            defs.append(f"    CHECK ({check})")
        lines.append(",\n".join(defs))
        lines.append(");")
        lines.append("")
    for table in plan.tables:
        for fk in table.foreign_keys:
            lines.append(
                f"ALTER TABLE {table.name} ADD FOREIGN KEY ({', '.join(fk.columns)}) "
                f"REFERENCES {fk.target_table} ({', '.join(fk.target_columns)});"
            )
    if any(t.foreign_keys for t in plan.tables):
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def capability_report(model: Model) -> CapabilityReport:
    entries: list[CapabilityEntry] = []
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        entries.append(CapabilityEntry("fact_type", fact.id, "postgres", CapabilityStatus.NATIVE_ENFORCED,
            mechanism=f"table {pg_name(fact.name)}; set semantics enforced by primary/unique key"))
    for hint in sorted(model.field_hints.values(), key=lambda h: h.field_fact_id):
        entries.append(CapabilityEntry("field_projection", hint.field_fact_id, "postgres", CapabilityStatus.NATIVE_ENFORCED,
            mechanism=f"column {pg_name(hint.field_name)} on owner table"))
    for c in sorted(model.constraints.values(), key=lambda c: c.id):
        if c.kind == ConstraintKind.UNIQUENESS:
            mechanism = "single-valued column projection" if c.fact_type_id in model.field_hints else "UNIQUE / PRIMARY KEY"
            entries.append(CapabilityEntry("uniqueness", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, mechanism))
        elif c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
            entries.append(CapabilityEntry("preferred_identifier", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, "PRIMARY KEY"))
        elif c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
            entries.append(CapabilityEntry("unordered_roles", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, "CHECK canonical role ordering"))
        elif c.kind == ConstraintKind.MANDATORY:
            if c.fact_type_id in model.field_hints:
                entries.append(CapabilityEntry("mandatory", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, "NOT NULL column"))
            else:
                entries.append(CapabilityEntry("mandatory", c.id, "postgres", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="total participation across a relationship is not enforced by the canonical table mapping"))
        elif c.kind == ConstraintKind.FREQUENCY:
            if c.max_frequency == 1:
                entries.append(CapabilityEntry("frequency", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, "UNIQUE over constrained role sequence"))
            else:
                entries.append(CapabilityEntry("frequency", c.id, "postgres", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="frequency bounds above one require cross-row counting/trigger logic"))
        elif c.kind == ConstraintKind.VALUE:
            if _value_constraint_is_used(model, c.object_type_id):
                entries.append(CapabilityEntry("value", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, "CHECK on every projected column using the value type"))
            else:
                entries.append(CapabilityEntry("value", c.id, "postgres", CapabilityStatus.METADATA_ONLY, reason="constrained value type is not projected by this model"))
        elif c.kind == ConstraintKind.SUBTYPE:
            entries.append(CapabilityEntry("subtype", c.id, "postgres", CapabilityStatus.NATIVE_ENFORCED, "table-per-type primary-key foreign key to supertype"))
        elif c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
            entries.append(CapabilityEntry(c.kind.value, c.id, "postgres", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="cross-table role-sequence set constraints require assertions/triggers or specialized FK transformations"))
        elif c.kind == ConstraintKind.RING:
            entries.append(CapabilityEntry(f"ring:{c.ring_kind}", c.id, "postgres", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="requires cross-row semantics beyond canonical constraints"))
        else:
            entries.append(CapabilityEntry(c.kind.value, c.id, "postgres", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="constraint retained in semantic manifest"))
    for reading in model.readings.values():
        if reading.fact_type_id not in model.field_hints:
            entries.append(CapabilityEntry("reading", reading.id, "postgres", CapabilityStatus.METADATA_ONLY, reason="conceptual reading is not executable relational schema"))
    return CapabilityReport("postgres", entries)


_CREATE_RE = re.compile(r"CREATE TABLE\s+(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*\((?P<body>.*?)\);", re.S | re.I)


def structural_recovery(sql: str) -> dict:
    tables: list[dict] = []
    for match in _CREATE_RE.finditer(sql):
        name = match.group("name")
        body = match.group("body")
        parts: list[str] = []
        buf: list[str] = []
        depth = 0
        for ch in body:
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            if ch == "," and depth == 0:
                parts.append("".join(buf).strip()); buf = []
            else:
                buf.append(ch)
        if "".join(buf).strip(): parts.append("".join(buf).strip())
        cols, fks, pks, uniques, checks = [], [], [], [], []
        for p in parts:
            up = p.upper()
            if up.startswith("PRIMARY KEY"): pks.append(p)
            elif up.startswith("FOREIGN KEY"): fks.append(p)
            elif up.startswith("UNIQUE"): uniques.append(p)
            elif up.startswith("CHECK"): checks.append(p)
            else:
                bits = p.split()
                if bits: cols.append({"name": bits[0], "definition": " ".join(bits[1:])})
        tables.append({"name": name, "columns": cols, "foreign_keys": fks, "primary_keys": pks, "uniques": uniques, "checks": checks})
    alter_re = re.compile(
        r"ALTER TABLE\s+(?P<table>[a-zA-Z_][a-zA-Z0-9_]*)\s+ADD FOREIGN KEY\s*\((?P<cols>[^)]*)\)\s+REFERENCES\s+(?P<target>[a-zA-Z_][a-zA-Z0-9_]*)\s*\((?P<tcols>[^)]*)\);", re.I,
    )
    table_by_name = {t["name"]: t for t in tables}
    for m in alter_re.finditer(sql):
        table = table_by_name.get(m.group("table"))
        if table is not None:
            table["foreign_keys"].append(f"FOREIGN KEY ({m.group('cols').strip()}) REFERENCES {m.group('target')} ({m.group('tcols').strip()})")
    return {
        "reader_scope": "canonical SQL projection format v1 emitted by factgraph",
        "tables": tables,
        "recoverable_without_metadata": ["table names", "column names and SQL types", "nullability", "primary/unique keys", "foreign-key targets", "CHECK constraints"],
        "not_reliably_recoverable_without_metadata": ["original object/value type names", "conceptual readings", "entity-vs-objectified-fact intent in all cases", "non-enforced conceptual constraints", "field sugar provenance"],
    }


def structural_recovery_json(sql: str) -> str:
    return json.dumps(structural_recovery(sql), indent=2, sort_keys=True) + "\n"


def recover_with_manifest(sql: str, manifest_json: str) -> Model:
    recovered = structural_recovery(sql)
    if not recovered["tables"]:
        raise ValueError("no canonical CREATE TABLE statements found")
    return Model.from_manifest_json(manifest_json)
