from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from typing import Any, Iterable

from .ids import slug
from .model import ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType
from .reporting import CapabilityStatus
from .targets import mongo, postgres, typedb


@dataclass(frozen=True)
class ConformanceCase:
    id: str
    target: str
    feature: str
    source_elements: tuple[str, ...]
    mode: str  # runtime | structural
    description: str
    setup: tuple[Any, ...] = ()
    steps: tuple[Any, ...] = ()
    static_pass: bool | None = None
    evidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_facts(model: Model):
    return sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.name)


def _entity_objects(model: Model):
    return sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.name)


def _scalar_marker(kind: str, variant: int) -> Any:
    """Portable JSON value for deterministic conformance populations."""
    if kind == "String":
        return ["a001", "m002", "z003"][variant % 3]
    if kind == "Int":
        return [101, 202, 909][variant % 3]
    if kind == "Decimal":
        return {"$fg_type": "decimal", "value": ["101.25", "202.50", "909.75"][variant % 3]}
    if kind == "Date":
        return {"$fg_type": "date", "value": ["2026-01-01", "2026-06-15", "2026-12-31"][variant % 3]}
    if kind == "UUID":
        return [
            "00000000-0000-0000-0000-000000000001",
            "70000000-0000-0000-0000-000000000002",
            "ffffffff-ffff-ffff-ffff-fffffffffff3",
        ][variant % 3]
    if kind == "Bool":
        return bool(variant % 2)
    if kind == "Timestamp":
        return {"$fg_type": "datetime", "value": [
            "2026-01-01T00:00:00+00:00",
            "2026-06-15T12:00:00+00:00",
            "2026-12-31T23:59:59+00:00",
        ][variant % 3]}
    if kind == "Float":
        return [1.25, 5.5, 9.75][variant % 3]
    raise ValueError(f"unsupported scalar kind {kind!r}")


def _value_for_type(model: Model, value_type_id: str, variant: int) -> Any:
    vt = model.object_types[value_type_id]
    assert isinstance(vt, ValueType)
    constraints = model.constraints_for_value(vt.id)
    for c in constraints:
        spec = c.value_spec or {}
        if spec.get("kind") == "oneof":
            vals = spec.get("values", [])
            if vals:
                raw = vals[variant % len(vals)]
                if vt.scalar_kind == "Decimal":
                    return {"$fg_type": "decimal", "value": str(raw)}
                if vt.scalar_kind == "Date":
                    return {"$fg_type": "date", "value": str(raw)}
                if vt.scalar_kind == "Timestamp":
                    return {"$fg_type": "datetime", "value": str(raw)}
                return raw
    for c in constraints:
        spec = c.value_spec or {}
        if spec.get("kind") == "range":
            lo, hi = spec["min"], spec["max"]
            if vt.scalar_kind == "Int":
                span = max(0, int(hi) - int(lo))
                return int(lo) + (variant % (span + 1 if span < 100000 else 3))
            if vt.scalar_kind in {"Float", "Decimal"}:
                raw = float(lo) + (float(hi) - float(lo)) * ([0.25, 0.5, 0.75][variant % 3])
                if vt.scalar_kind == "Decimal":
                    return {"$fg_type": "decimal", "value": str(raw)}
                return raw
    return _scalar_marker(vt.scalar_kind, variant)


def _pg_value_for_type(model: Model, value_type_id: str, variant: int) -> str:
    vt = model.object_types[value_type_id]
    assert isinstance(vt, ValueType)
    value = _value_for_type(model, value_type_id, variant)
    if isinstance(value, dict) and "$fg_type" in value:
        value = value["value"]
    return postgres._sql_literal(value, vt.scalar_kind)


def _pg_value(sql_type: str, variant: int) -> str:
    base = sql_type.upper()
    if "GENERATED ALWAYS AS IDENTITY" in base:
        raise ValueError("identity values should be omitted")
    if base.startswith("TEXT"):
        return "'" + ["a001", "m002", "z003"][variant % 3] + "'"
    if base.startswith("UUID"):
        return "'" + [
            "00000000-0000-0000-0000-000000000001",
            "70000000-0000-0000-0000-000000000002",
            "ffffffff-ffff-ffff-ffff-fffffffffff3",
        ][variant % 3] + "'::uuid"
    if base.startswith("INTEGER") or base == "BIGINT":
        return str([101, 202, 909][variant % 3])
    if base.startswith("NUMERIC"):
        return ["101.25", "202.50", "909.75"][variant % 3]
    if base.startswith("DATE"):
        return "DATE '" + ["2026-01-01", "2026-06-15", "2026-12-31"][variant % 3] + "'"
    if base.startswith("BOOLEAN"):
        return "TRUE" if variant % 2 else "FALSE"
    if base.startswith("TIMESTAMPTZ"):
        return "TIMESTAMPTZ '" + [
            "2026-01-01T00:00:00+00:00",
            "2026-06-15T12:00:00+00:00",
            "2026-12-31T23:59:59+00:00",
        ][variant % 3] + "'"
    if base.startswith("DOUBLE PRECISION"):
        return str([1.25, 5.5, 9.75][variant % 3])
    raise ValueError(f"cannot make conformance value for SQL type {sql_type!r}")


def _pg_insert(table: str, row: dict[str, str]) -> str:
    if not row:
        return f"INSERT INTO {table} DEFAULT VALUES;"
    cols = ", ".join(row)
    vals = ", ".join(row[c] for c in row)
    return f"INSERT INTO {table} ({cols}) VALUES ({vals});"


def _pg_table_map(model: Model):
    plan = postgres.build_plan(model)
    return {t.name: t for t in plan.tables}


def _pg_entity_rows(model: Model) -> dict[str, list[dict[str, str]]]:
    tables = _pg_table_map(model)
    rows: dict[str, list[dict[str, str]]] = {}
    for entity in _entity_objects(model):
        table = tables[slug(entity.name)]
        variants: list[dict[str, str]] = []
        id_hints = postgres._identifier_hints(model, entity.id)
        hint_by_col = {slug(h.field_name): h for h in [*id_hints, *postgres._hints_for_owner(model, entity.id)]}
        for variant in range(3):
            row: dict[str, str] = {}
            for col in table.columns:
                if "GENERATED ALWAYS AS IDENTITY" in col.sql_type.upper():
                    continue
                hint = hint_by_col.get(col.name)
                if hint is not None:
                    row[col.name] = _pg_value_for_type(model, hint.value_type_id, variant)
                else:
                    row[col.name] = _pg_value(col.sql_type, variant)
            variants.append(row)
        rows[entity.id] = variants
    return rows


def _pg_role_value_map(model: Model, role, variant: int, entity_rows: dict[str, list[dict[str, str]]]) -> dict[str, str]:
    player = model.object_types[role.player_id]
    cols = postgres._role_columns(model, role)  # same package: conformance tests the canonical target mapping
    if isinstance(player, ValueType):
        return {cols[0][0]: _pg_value_for_type(model, player.id, variant)}
    if isinstance(player, EntityType):
        key = postgres._entity_key(model, player)
        source = entity_rows[player.id][variant % 3]
        return {target_col: source[key_col] for (target_col, _), (key_col, _) in zip(cols, key)}
    if isinstance(player, ObjectifiedFactType):
        return {cols[0][0]: str(variant + 1)}
    raise TypeError(player)


def _pg_fact_row(model: Model, fact, variants: dict[str, int], entity_rows: dict[str, list[dict[str, str]]]) -> dict[str, str]:
    table = _pg_table_map(model)[slug(fact.name)]
    row: dict[str, str] = {}
    for role in sorted(fact.roles, key=lambda r: r.ordinal):
        row.update(_pg_role_value_map(model, role, variants.get(role.id, 0), entity_rows))
    obj = model.objectification_for_fact(fact.id)
    if obj is not None:
        for hint in sorted((h for h in model.field_hints.values() if h.owner_object_type_id == obj.id), key=lambda h: h.field_name):
            vt = model.object_types[hint.value_type_id]
            assert isinstance(vt, ValueType)
            row[slug(hint.field_name)] = _pg_value_for_type(model, vt.id, 0)
    # omit identity column intentionally
    expected = {c.name for c in table.columns if "GENERATED ALWAYS AS IDENTITY" not in c.sql_type.upper()}
    if set(row) != expected:
        missing = sorted(expected - set(row))
        extra = sorted(set(row) - expected)
        raise AssertionError(f"PostgreSQL conformance row mismatch for {fact.name}: missing={missing} extra={extra}")
    return row


def _subtype_depth(model: Model, entity: EntityType) -> int:
    depth = 0
    cur = entity.id
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        sup = model.supertype_of(cur)
        if not isinstance(sup, EntityType):
            break
        depth += 1
        cur = sup.id
    return depth


def _pg_entity_setup(model: Model, exclude: Iterable[str] = ()) -> list[str]:
    excluded = set(exclude)
    rows = _pg_entity_rows(model)
    out: list[str] = []
    for entity in sorted(_entity_objects(model), key=lambda e: (_subtype_depth(model, e), e.name)):
        if entity.id in excluded:
            continue
        table = slug(entity.name)
        for row in rows[entity.id]:
            out.append(_pg_insert(table, row))
    return out


def _objectified_dependencies(model: Model, fact) -> list:
    out = []
    seen: set[str] = set()

    def visit(f):
        for role in f.roles:
            player = model.object_types[role.player_id]
            if isinstance(player, ObjectifiedFactType):
                dep = model.fact_types[player.fact_type_id]
                if dep.id not in seen:
                    visit(dep)
                    seen.add(dep.id)
                    out.append(dep)

    visit(fact)
    return out


def _invalid_pg_for_value_constraint(model: Model, constraint) -> str:
    vt = model.object_types[constraint.object_type_id]
    assert isinstance(vt, ValueType)
    spec = constraint.value_spec or {}
    if spec.get("kind") == "range":
        value = spec["max"] + 1
    elif spec.get("kind") == "oneof":
        values = spec.get("values", [])
        if vt.scalar_kind in {"Int", "Decimal", "Float"}:
            value = max(values) + 1000
        elif vt.scalar_kind == "Bool":
            # oneof on Bool can only exclude one of the two values; choose the other if possible
            value = not bool(values[0])
        else:
            value = "__factgraph_invalid__"
    else:
        raise ValueError(f"unsupported value constraint {spec}")
    return postgres._sql_literal(value, vt.scalar_kind)


def _invalid_mongo_for_value_constraint(model: Model, constraint) -> Any:
    vt = model.object_types[constraint.object_type_id]
    assert isinstance(vt, ValueType)
    spec = constraint.value_spec or {}
    if spec.get("kind") == "range":
        raw = spec["max"] + 1
    elif spec.get("kind") == "oneof":
        values = spec.get("values", [])
        if vt.scalar_kind in {"Int", "Decimal", "Float"}:
            raw = max(values) + 1000
        elif vt.scalar_kind == "Bool":
            raw = not bool(values[0])
        else:
            raw = "__factgraph_invalid__"
    else:
        raise ValueError(f"unsupported value constraint {spec}")
    if vt.scalar_kind == "Decimal":
        return {"$fg_type": "decimal", "value": str(raw)}
    if vt.scalar_kind == "Date":
        return {"$fg_type": "date", "value": str(raw)}
    if vt.scalar_kind == "Timestamp":
        return {"$fg_type": "datetime", "value": str(raw)}
    return raw


def _direct_value_role_use(model: Model, value_type_id: str):
    for fact in _source_facts(model):
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            if role.player_id == value_type_id:
                return fact, role
    return None


def _field_hint_for_value(model: Model, value_type_id: str):
    hints = [h for h in model.field_hints.values() if h.value_type_id == value_type_id]
    def rank(h):
        owner = model.object_types[h.owner_object_type_id]
        if isinstance(owner, EntityType) and model.subtype_constraint(owner.id) is None:
            bucket = 0
        elif isinstance(owner, EntityType):
            bucket = 1
        else:
            bucket = 2
        return (bucket, h.field_fact_id)
    return sorted(hints, key=rank)[0] if hints else None


def postgres_cases(model: Model) -> list[ConformanceCase]:
    entity_rows = _pg_entity_rows(model)
    table_map = _pg_table_map(model)
    cases: list[ConformanceCase] = []

    # Structural field projections and the field-origin single-valued uniqueness invariant.
    for hint in sorted(model.field_hints.values(), key=lambda h: h.field_fact_id):
        owner = model.object_types[hint.owner_object_type_id]
        owner_table = slug(model.fact_types[owner.fact_type_id].name) if isinstance(owner, ObjectifiedFactType) else slug(owner.name)
        col = slug(hint.field_name)
        table = table_map[owner_table]
        present = sum(1 for c in table.columns if c.name == col) == 1
        cases.append(ConformanceCase(
            f"pg-structure-field-{hint.field_fact_id}", "postgres", "field_projection", (hint.field_fact_id,), "structural",
            f"Field {hint.field_name} is projected exactly once on {owner_table}.", static_pass=present,
            evidence=f"column {owner_table}.{col} occurs exactly once" if present else f"column projection mismatch for {owner_table}.{col}",
        ))
        uniques = [c for c in model.constraints_for_fact(hint.field_fact_id, ConstraintKind.UNIQUENESS)]
        for c in uniques:
            cases.append(ConformanceCase(
                f"pg-structure-single-valued-{c.id}", "postgres", "uniqueness", (c.id,), "structural",
                "Field sugar normalizes to one scalar column per owner row; two simultaneous values cannot be represented in one row.",
                static_pass=present, evidence=f"single column {owner_table}.{col}",
            ))

    # Preferred identifiers: first insert succeeds, duplicate key is rejected.
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.PREFERRED_IDENTIFIER), key=lambda c: c.id):
        entity = model.object_types[c.object_type_id]
        assert isinstance(entity, EntityType)
        row = entity_rows[entity.id][0]
        cases.append(ConformanceCase(
            f"pg-preferred-id-{c.id}", "postgres", "preferred_identifier", (c.id,), "runtime",
            f"PostgreSQL rejects a duplicate preferred identifier for {entity.name}.",
            steps=(
                {"sql": _pg_insert(slug(entity.name), row), "expect": "accept"},
                {"sql": _pg_insert(slug(entity.name), row), "expect": "reject"},
            ),
        ))

    # Value constraints: exercise one projected field with a valid then invalid value.
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.VALUE), key=lambda c: c.id):
        hint = _field_hint_for_value(model, c.object_type_id)
        if hint is not None:
            owner = model.object_types[hint.owner_object_type_id]
            if isinstance(owner, EntityType):
                row = dict(entity_rows[owner.id][0])
                invalid = dict(entity_rows[owner.id][1])
                invalid[slug(hint.field_name)] = _invalid_pg_for_value_constraint(model, c)
                setup = ()
                if model.subtype_constraint(owner.id) is not None:
                    sup = model.supertype_of(owner.id)
                    if isinstance(sup, EntityType):
                        setup = (_pg_insert(slug(sup.name), entity_rows[sup.id][0]), _pg_insert(slug(sup.name), entity_rows[sup.id][1]))
                cases.append(ConformanceCase(
                    f"pg-value-{c.id}", "postgres", "value", (c.id,), "runtime",
                    f"PostgreSQL CHECK rejects a value outside the declared domain for {model.object_types[c.object_type_id].name}.",
                    setup=setup,
                    steps=(
                        {"sql": _pg_insert(slug(owner.name), row), "expect": "accept"},
                        {"sql": _pg_insert(slug(owner.name), invalid), "expect": "reject"},
                    ),
                ))
            elif isinstance(owner, ObjectifiedFactType):
                fact = model.fact_types[owner.fact_type_id]
                setup = _pg_entity_setup(model)
                for dep in _objectified_dependencies(model, fact):
                    setup.append(_pg_insert(slug(dep.name), _pg_fact_row(model, dep, {}, entity_rows)))
                valid = _pg_fact_row(model, fact, {}, entity_rows)
                changed = {fact.roles[0].id: 1} if fact.roles else {}
                invalid = _pg_fact_row(model, fact, changed, entity_rows)
                invalid[slug(hint.field_name)] = _invalid_pg_for_value_constraint(model, c)
                cases.append(ConformanceCase(
                    f"pg-value-{c.id}", "postgres", "value", (c.id,), "runtime",
                    f"PostgreSQL CHECK rejects an objectified-fact field value outside the declared domain for {model.object_types[c.object_type_id].name}.",
                    setup=tuple(setup),
                    steps=(
                        {"sql": _pg_insert(slug(fact.name), valid), "expect": "accept"},
                        {"sql": _pg_insert(slug(fact.name), invalid), "expect": "reject"},
                    ),
                ))
            continue
        direct = _direct_value_role_use(model, c.object_type_id)
        if direct is not None:
            fact, role = direct
            setup = _pg_entity_setup(model)
            for dep in _objectified_dependencies(model, fact):
                setup.append(_pg_insert(slug(dep.name), _pg_fact_row(model, dep, {}, entity_rows)))
            valid = _pg_fact_row(model, fact, {}, entity_rows)
            invalid = _pg_fact_row(model, fact, {role.id: 1}, entity_rows)
            col = postgres._role_columns(model, role)[0][0]
            invalid[col] = _invalid_pg_for_value_constraint(model, c)
            cases.append(ConformanceCase(
                f"pg-value-{c.id}", "postgres", "value", (c.id,), "runtime",
                f"PostgreSQL CHECK rejects a direct fact-role value outside the domain for {model.object_types[c.object_type_id].name}.",
                setup=tuple(setup),
                steps=(
                    {"sql": _pg_insert(slug(fact.name), valid), "expect": "accept"},
                    {"sql": _pg_insert(slug(fact.name), invalid), "expect": "reject"},
                ),
            ))

    # Subtyping: subtype row identity must refer to an existing supertype row.
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.SUBTYPE), key=lambda c: c.id):
        sub = model.object_types[c.subtype_id]
        sup = model.object_types[c.supertype_id]
        if isinstance(sub, EntityType) and isinstance(sup, EntityType):
            super_row = entity_rows[sup.id][0]
            valid_sub = entity_rows[sub.id][0]
            invalid_sub = entity_rows[sub.id][1]
            cases.append(ConformanceCase(
                f"pg-subtype-{c.id}", "postgres", "subtype", (c.id,), "runtime",
                f"PostgreSQL table-per-type FK requires every {sub.name} identity to exist as {sup.name}.",
                setup=(_pg_insert(slug(sup.name), super_row),),
                steps=(
                    {"sql": _pg_insert(slug(sub.name), valid_sub), "expect": "accept"},
                    {"sql": _pg_insert(slug(sub.name), invalid_sub), "expect": "reject"},
                ),
            ))

    # Field-origin mandatory constraints: omit the required field and expect rejection.
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY and c.fact_type_id in model.field_hints), key=lambda c: c.id):
        hint = model.field_hints[c.fact_type_id]
        owner = model.object_types[hint.owner_object_type_id]
        owner_table = slug(model.fact_types[owner.fact_type_id].name) if isinstance(owner, ObjectifiedFactType) else slug(owner.name)
        if isinstance(owner, ObjectifiedFactType):
            # Relationship fields are tested as part of objectified-fact insertion; generate a row with this field omitted.
            fact = model.fact_types[owner.fact_type_id]
            setup = _pg_entity_setup(model)
            for dep in _objectified_dependencies(model, fact):
                setup.append(_pg_insert(slug(dep.name), _pg_fact_row(model, dep, {}, entity_rows)))
            valid = _pg_fact_row(model, fact, {}, entity_rows)
            changed = {fact.roles[0].id: 1} if fact.roles else {}
            invalid = _pg_fact_row(model, fact, changed, entity_rows)
            invalid.pop(slug(hint.field_name), None)
        else:
            assert isinstance(owner, EntityType)
            valid = dict(entity_rows[owner.id][0])
            invalid = dict(valid)
            invalid.pop(slug(hint.field_name), None)
            setup = []
        cases.append(ConformanceCase(
            f"pg-mandatory-{c.id}", "postgres", "mandatory", (c.id,), "runtime",
            f"PostgreSQL NOT NULL/primary-key structure rejects omission of required field {hint.field_name}.",
            setup=tuple(setup),
            steps=(
                {"sql": _pg_insert(owner_table, valid), "expect": "accept"},
                # fresh schema is not reset between steps: invalid row uses variant 1 to avoid duplicate key masking.
                {"sql": _pg_insert(owner_table, invalid), "expect": "reject"},
            ) if isinstance(owner, ObjectifiedFactType) else (
                {"sql": _pg_insert(owner_table, invalid), "expect": "reject"},
            ),
        ))

    # Deliberate gap probes: constraints reported as represented_not_enforced should remain unenforced.
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY and c.fact_type_id not in model.field_hints), key=lambda c: c.id):
        fact = model.fact_types[c.fact_type_id]
        role = next(r for r in fact.roles if r.id == c.role_ids[0])
        player = model.object_types[role.player_id]
        if isinstance(player, EntityType):
            row = entity_rows[player.id][0]
            cases.append(ConformanceCase(
                f"pg-gap-mandatory-{c.id}", "postgres", "mandatory_not_enforced", (c.id,), "runtime",
                f"Total participation for {fact.name}.{role.name} is intentionally not enforced by the canonical PostgreSQL mapping.",
                steps=(
                    {"sql": _pg_insert(slug(player.name), row), "expect": "accept"},
                    {"sql": f"SELECT COUNT(*) FROM {slug(fact.name)};", "expect_scalar": 0},
                ),
            ))

    for fact in _source_facts(model):
        setup = _pg_entity_setup(model)
        for dep in _objectified_dependencies(model, fact):
            setup.append(_pg_insert(slug(dep.name), _pg_fact_row(model, dep, {}, entity_rows)))
        base = _pg_fact_row(model, fact, {}, entity_rows)
        cases.append(ConformanceCase(
            f"pg-fact-set-{fact.id}", "postgres", "fact_type", (fact.id,), "runtime",
            f"Complete fact tuple for {fact.name} has set semantics: duplicate tuple is rejected.",
            setup=tuple(setup),
            steps=(
                {"sql": _pg_insert(slug(fact.name), base), "expect": "accept"},
                {"sql": _pg_insert(slug(fact.name), base), "expect": "reject"},
            ),
        ))

        role_ids = [r.id for r in fact.roles]
        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNIQUENESS):
            variants1 = {rid: 0 for rid in role_ids}
            variants2 = {rid: 0 for rid in role_ids}
            unconstrained = [rid for rid in role_ids if rid not in c.role_ids]
            if unconstrained:
                variants2[unconstrained[0]] = 1
            row1 = _pg_fact_row(model, fact, variants1, entity_rows)
            row2 = _pg_fact_row(model, fact, variants2, entity_rows)
            cases.append(ConformanceCase(
                f"pg-unique-{c.id}", "postgres", "uniqueness", (c.id,), "runtime",
                f"Declared uniqueness for {fact.name} is rejected when constrained roles repeat.",
                setup=tuple(setup),
                steps=(
                    {"sql": _pg_insert(slug(fact.name), row1), "expect": "accept"},
                    {"sql": _pg_insert(slug(fact.name), row2), "expect": "reject"},
                ),
            ))

        for c in model.constraints_for_fact(fact.id, ConstraintKind.FREQUENCY):
            if c.max_frequency != 1:
                continue
            variants1 = {rid: 0 for rid in role_ids}
            variants2 = {rid: 0 for rid in role_ids}
            unconstrained = [rid for rid in role_ids if rid not in c.role_ids]
            if unconstrained:
                variants2[unconstrained[0]] = 1
            row1 = _pg_fact_row(model, fact, variants1, entity_rows)
            row2 = _pg_fact_row(model, fact, variants2, entity_rows)
            cases.append(ConformanceCase(
                f"pg-frequency-{c.id}", "postgres", "frequency", (c.id,), "runtime",
                f"Frequency max=1 for {fact.name} is enforced by uniqueness over the constrained roles.",
                setup=tuple(setup),
                steps=(
                    {"sql": _pg_insert(slug(fact.name), row1), "expect": "accept"},
                    {"sql": _pg_insert(slug(fact.name), row2), "expect": "reject"},
                ),
            ))

        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNORDERED_ROLE_GROUP):
            canonical_variants = {rid: idx for idx, rid in enumerate(c.role_ids)}
            reversed_variants = dict(canonical_variants)
            vals = list(reversed(canonical_variants.values()))
            for rid, val in zip(c.role_ids, vals):
                reversed_variants[rid] = val
            valid = _pg_fact_row(model, fact, canonical_variants, entity_rows)
            invalid = _pg_fact_row(model, fact, reversed_variants, entity_rows)
            cases.append(ConformanceCase(
                f"pg-unordered-{c.id}", "postgres", "unordered_roles", (c.id,), "runtime",
                f"Canonical role ordering for unordered roles on {fact.name} accepts ordered input and rejects reversed input.",
                setup=tuple(setup),
                steps=(
                    {"sql": _pg_insert(slug(fact.name), valid), "expect": "accept"},
                    {"sql": _pg_insert(slug(fact.name), invalid), "expect": "reject"},
                ),
            ))

        for c in model.constraints_for_fact(fact.id, ConstraintKind.RING):
            if c.ring_kind == "symmetric" and len(fact.roles) == 2:
                forward = _pg_fact_row(model, fact, {fact.roles[0].id: 0, fact.roles[1].id: 1}, entity_rows)
                reverse = _pg_fact_row(model, fact, {fact.roles[0].id: 1, fact.roles[1].id: 0}, entity_rows)
                where = " AND ".join(f"{k} = {v}" for k, v in reverse.items())
                cases.append(ConformanceCase(
                    f"pg-gap-ring-{c.id}", "postgres", f"ring:{c.ring_kind}_not_enforced", (c.id,), "runtime",
                    f"Logical symmetry on {fact.name} is retained semantically but PostgreSQL does not auto-create the reverse fact.",
                    setup=tuple(setup),
                    steps=(
                        {"sql": _pg_insert(slug(fact.name), forward), "expect": "accept"},
                        {"sql": f"SELECT COUNT(*) FROM {slug(fact.name)} WHERE {where};", "expect_scalar": 0},
                    ),
                ))
    # Rich frequency bounds and cross-fact set constraints are deliberately represented but not enforced in v0.3.
    pg_report = postgres.capability_report(model)
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.FREQUENCY and c.max_frequency != 1), key=lambda c: c.id):
        entry = next((e for e in pg_report.entries if e.source_element == c.id), None)
        ok = entry is not None and entry.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED
        cases.append(ConformanceCase(
            f"pg-gap-frequency-{c.id}", "postgres", "frequency_not_enforced", (c.id,), "structural",
            "PostgreSQL adapter explicitly reports frequency bounds above one as represented but not enforced.",
            static_pass=ok, evidence=entry.reason if entry else "missing capability entry",
        ))
    for c in sorted((c for c in model.constraints.values() if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}), key=lambda c: c.id):
        entry = next((e for e in pg_report.entries if e.source_element == c.id), None)
        ok = entry is not None and entry.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED
        cases.append(ConformanceCase(
            f"pg-gap-{c.kind.value}-{c.id}", "postgres", f"{c.kind.value}_not_enforced", (c.id,), "structural",
            f"PostgreSQL adapter explicitly reports {c.kind.value} as represented but not enforced.",
            static_pass=ok, evidence=entry.reason if entry else "missing capability entry",
        ))
    return cases


def _mongo_value(kind: str, variant: int) -> Any:
    return _scalar_marker(kind, variant)


def _mongo_entity_docs(model: Model) -> dict[str, list[dict[str, Any]]]:
    docs: dict[str, list[dict[str, Any]]] = {}
    for entity in _entity_objects(model):
        variants: list[dict[str, Any]] = []
        hints = mongo._hints(model, entity.id)
        ids = mongo._ids(model, entity.id)
        for variant in range(3):
            doc: dict[str, Any] = {}
            if not ids:
                doc["_id"] = {"$fg_type": "objectId", "value": f"{variant + 1:024x}"}
            for h in ids:
                vt = model.object_types[h.value_type_id]
                assert isinstance(vt, ValueType)
                doc[slug(h.field_name)] = _value_for_type(model, vt.id, variant)
            for h in hints:
                vt = model.object_types[h.value_type_id]
                assert isinstance(vt, ValueType)
                doc[slug(h.field_name)] = _value_for_type(model, vt.id, variant)
            variants.append(doc)
        docs[entity.id] = variants
    return docs


def _mongo_role_values(model: Model, role, variant: int, entity_docs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    player = model.object_types[role.player_id]
    base = slug(role.name)
    if isinstance(player, ValueType):
        return {base: _value_for_type(model, player.id, variant)}
    if isinstance(player, EntityType):
        ids = mongo._ids(model, player.id)
        source = entity_docs[player.id][variant % 3]
        if len(ids) == 1:
            return {f"{base}_id": source[slug(ids[0].field_name)]}
        if len(ids) > 1:
            return {f"{base}__{slug(h.field_name)}": source[slug(h.field_name)] for h in ids}
        return {f"{base}_id": source["_id"]}
    if isinstance(player, ObjectifiedFactType):
        return {f"{base}_id": {"$fg_type": "objectId", "value": f"{variant + 1:024x}"}}
    raise TypeError(player)


def _mongo_fact_doc(model: Model, fact, variants: dict[str, int], entity_docs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    for role in sorted(fact.roles, key=lambda r: r.ordinal):
        doc.update(_mongo_role_values(model, role, variants.get(role.id, 0), entity_docs))
    obj = model.objectification_for_fact(fact.id)
    if obj is not None:
        doc["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000001"}
        for h in mongo._hints(model, obj.id):
            vt = model.object_types[h.value_type_id]
            assert isinstance(vt, ValueType)
            doc[slug(h.field_name)] = _value_for_type(model, vt.id, 0)
    return doc


def _mongo_entity_setup(model: Model, exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
    excluded = set(exclude)
    docs = _mongo_entity_docs(model)
    out: list[dict[str, Any]] = []
    for entity in _entity_objects(model):
        if entity.id in excluded:
            continue
        for doc in docs[entity.id]:
            out.append({"op": "insert_one", "collection": slug(entity.name), "document": doc})
    return out


def mongo_cases(model: Model) -> list[ConformanceCase]:
    entity_docs = _mongo_entity_docs(model)
    plan = mongo.build_plan(model)
    coll_map = {c["name"]: c for c in plan["collections"]}
    cases: list[ConformanceCase] = []

    for hint in sorted(model.field_hints.values(), key=lambda h: h.field_fact_id):
        owner = model.object_types[hint.owner_object_type_id]
        owner_coll = slug(model.fact_types[owner.fact_type_id].name) if isinstance(owner, ObjectifiedFactType) else slug(owner.name)
        prop = slug(hint.field_name)
        present = prop in coll_map[owner_coll]["validator"]["$jsonSchema"]["properties"]
        cases.append(ConformanceCase(
            f"mongo-structure-field-{hint.field_fact_id}", "mongo", "field_projection", (hint.field_fact_id,), "structural",
            f"Field {hint.field_name} is projected into the MongoDB document schema.", static_pass=present,
            evidence=f"property {owner_coll}.{prop} present" if present else f"property {owner_coll}.{prop} missing",
        ))
        for c in model.constraints_for_fact(hint.field_fact_id, ConstraintKind.UNIQUENESS):
            cases.append(ConformanceCase(
                f"mongo-structure-single-valued-{c.id}", "mongo", "uniqueness", (c.id,), "structural",
                "Field sugar becomes one scalar property per owner document.", static_pass=present,
                evidence=f"single property {owner_coll}.{prop}",
            ))

    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.PREFERRED_IDENTIFIER), key=lambda c: c.id):
        entity = model.object_types[c.object_type_id]
        assert isinstance(entity, EntityType)
        doc = entity_docs[entity.id][0]
        cases.append(ConformanceCase(
            f"mongo-preferred-id-{c.id}", "mongo", "preferred_identifier", (c.id,), "runtime",
            f"MongoDB unique index rejects duplicate preferred identifier for {entity.name}.",
            steps=(
                {"op": "insert_one", "collection": slug(entity.name), "document": doc, "expect": "accept"},
                {"op": "insert_one", "collection": slug(entity.name), "document": doc, "expect": "reject"},
            ),
        ))

    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.VALUE), key=lambda c: c.id):
        if not mongo._value_constraint_native(model, c):
            vt = model.object_types[c.object_type_id]
            cases.append(ConformanceCase(
                f"mongo-gap-value-{c.id}", "mongo", "value_not_enforced", (c.id,), "structural",
                f"MongoDB adapter explicitly reports this {vt.name} value constraint as represented but not enforced in the canonical pure JSON target spec.",
                static_pass=True, evidence="BSON-native Decimal/Date/Timestamp constraint literals require a representation layer intentionally absent from v0.3 pure JSON spec",
            ))
            continue
        hint = _field_hint_for_value(model, c.object_type_id)
        if hint is not None:
            owner = model.object_types[hint.owner_object_type_id]
            if isinstance(owner, EntityType):
                valid = dict(entity_docs[owner.id][0])
                invalid = dict(entity_docs[owner.id][1])
                invalid[slug(hint.field_name)] = _invalid_mongo_for_value_constraint(model, c)
                cases.append(ConformanceCase(
                    f"mongo-value-{c.id}", "mongo", "value", (c.id,), "runtime",
                    f"MongoDB document validation rejects a value outside the declared domain for {model.object_types[c.object_type_id].name}.",
                    steps=(
                        {"op": "insert_one", "collection": slug(owner.name), "document": valid, "expect": "accept"},
                        {"op": "insert_one", "collection": slug(owner.name), "document": invalid, "expect": "reject"},
                    ),
                ))
            continue
        direct = _direct_value_role_use(model, c.object_type_id)
        if direct is not None:
            fact, role = direct
            setup = _mongo_entity_setup(model)
            for dep in _objectified_dependencies(model, fact):
                setup.append({"op": "insert_one", "collection": slug(dep.name), "document": _mongo_fact_doc(model, dep, {}, entity_docs)})
            valid = _mongo_fact_doc(model, fact, {}, entity_docs)
            invalid = _mongo_fact_doc(model, fact, {role.id: 1}, entity_docs)
            field = next(iter(mongo._role_properties(model, role)))
            invalid[field] = _invalid_mongo_for_value_constraint(model, c)
            if "_id" in invalid:
                invalid["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000002"}
            cases.append(ConformanceCase(
                f"mongo-value-{c.id}", "mongo", "value", (c.id,), "runtime",
                f"MongoDB document validation rejects a direct fact-role value outside the domain for {model.object_types[c.object_type_id].name}.",
                setup=tuple(setup),
                steps=(
                    {"op": "insert_one", "collection": slug(fact.name), "document": valid, "expect": "accept"},
                    {"op": "insert_one", "collection": slug(fact.name), "document": invalid, "expect": "reject"},
                ),
            ))

    # Mongo carries inherited identifier shape for subtypes, but deliberately does not claim cross-collection membership enforcement.
    mongo_report = mongo.capability_report(model)
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.SUBTYPE), key=lambda c: c.id):
        sub = model.object_types[c.subtype_id]
        if isinstance(sub, EntityType):
            cases.append(ConformanceCase(
                f"mongo-gap-subtype-{c.id}", "mongo", "subtype_not_enforced", (c.id,), "runtime",
                f"MongoDB accepts a {sub.name} document without a matching supertype document; capability report must not claim enforcement.",
                steps=({"op": "insert_one", "collection": slug(sub.name), "document": entity_docs[sub.id][0], "expect": "accept"},),
            ))

    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY and c.fact_type_id in model.field_hints), key=lambda c: c.id):
        hint = model.field_hints[c.fact_type_id]
        owner = model.object_types[hint.owner_object_type_id]
        owner_coll = slug(model.fact_types[owner.fact_type_id].name) if isinstance(owner, ObjectifiedFactType) else slug(owner.name)
        if isinstance(owner, ObjectifiedFactType):
            fact = model.fact_types[owner.fact_type_id]
            setup = _mongo_entity_setup(model)
            for dep in _objectified_dependencies(model, fact):
                setup.append({"op": "insert_one", "collection": slug(dep.name), "document": _mongo_fact_doc(model, dep, {}, entity_docs)})
            valid = _mongo_fact_doc(model, fact, {}, entity_docs)
            changed = {fact.roles[0].id: 1} if fact.roles else {}
            invalid = _mongo_fact_doc(model, fact, changed, entity_docs)
            invalid["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000002"}
            invalid.pop(slug(hint.field_name), None)
            steps = (
                {"op": "insert_one", "collection": owner_coll, "document": valid, "expect": "accept"},
                {"op": "insert_one", "collection": owner_coll, "document": invalid, "expect": "reject"},
            )
        else:
            assert isinstance(owner, EntityType)
            valid = entity_docs[owner.id][0]
            invalid = dict(valid)
            invalid.pop(slug(hint.field_name), None)
            setup = []
            steps = ({"op": "insert_one", "collection": owner_coll, "document": invalid, "expect": "reject"},)
        cases.append(ConformanceCase(
            f"mongo-mandatory-{c.id}", "mongo", "mandatory", (c.id,), "runtime",
            f"MongoDB document validation rejects omission of required field {hint.field_name}.",
            setup=tuple(setup), steps=steps,
        ))

    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.MANDATORY and c.fact_type_id not in model.field_hints), key=lambda c: c.id):
        fact = model.fact_types[c.fact_type_id]
        role = next(r for r in fact.roles if r.id == c.role_ids[0])
        player = model.object_types[role.player_id]
        if isinstance(player, EntityType):
            cases.append(ConformanceCase(
                f"mongo-gap-mandatory-{c.id}", "mongo", "mandatory_not_enforced", (c.id,), "runtime",
                f"Total participation for {fact.name}.{role.name} is intentionally not a document-local MongoDB validator rule.",
                steps=(
                    {"op": "insert_one", "collection": slug(player.name), "document": entity_docs[player.id][0], "expect": "accept"},
                    {"op": "count_documents", "collection": slug(fact.name), "filter": {}, "expect_scalar": 0},
                ),
            ))

    for fact in _source_facts(model):
        setup = _mongo_entity_setup(model)
        for dep in _objectified_dependencies(model, fact):
            setup.append({"op": "insert_one", "collection": slug(dep.name), "document": _mongo_fact_doc(model, dep, {}, entity_docs)})
        base = _mongo_fact_doc(model, fact, {}, entity_docs)
        duplicate = dict(base)
        if "_id" in duplicate:
            duplicate["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000002"}
        cases.append(ConformanceCase(
            f"mongo-fact-set-{fact.id}", "mongo", "fact_type", (fact.id,), "runtime",
            f"Complete fact tuple for {fact.name} has set semantics via a unique index.",
            setup=tuple(setup),
            steps=(
                {"op": "insert_one", "collection": slug(fact.name), "document": base, "expect": "accept"},
                {"op": "insert_one", "collection": slug(fact.name), "document": duplicate, "expect": "reject"},
            ),
        ))

        role_ids = [r.id for r in fact.roles]
        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNIQUENESS):
            variants1 = {rid: 0 for rid in role_ids}
            variants2 = {rid: 0 for rid in role_ids}
            unconstrained = [rid for rid in role_ids if rid not in c.role_ids]
            if unconstrained:
                variants2[unconstrained[0]] = 1
            row1 = _mongo_fact_doc(model, fact, variants1, entity_docs)
            row2 = _mongo_fact_doc(model, fact, variants2, entity_docs)
            if "_id" in row2:
                row2["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000002"}
            cases.append(ConformanceCase(
                f"mongo-unique-{c.id}", "mongo", "uniqueness", (c.id,), "runtime",
                f"Declared uniqueness for {fact.name} rejects repeated constrained-role values.",
                setup=tuple(setup),
                steps=(
                    {"op": "insert_one", "collection": slug(fact.name), "document": row1, "expect": "accept"},
                    {"op": "insert_one", "collection": slug(fact.name), "document": row2, "expect": "reject"},
                ),
            ))

        for c in model.constraints_for_fact(fact.id, ConstraintKind.FREQUENCY):
            if c.max_frequency != 1:
                continue
            variants1 = {rid: 0 for rid in role_ids}
            variants2 = {rid: 0 for rid in role_ids}
            unconstrained = [rid for rid in role_ids if rid not in c.role_ids]
            if unconstrained:
                variants2[unconstrained[0]] = 1
            row1 = _mongo_fact_doc(model, fact, variants1, entity_docs)
            row2 = _mongo_fact_doc(model, fact, variants2, entity_docs)
            if "_id" in row2:
                row2["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000002"}
            cases.append(ConformanceCase(
                f"mongo-frequency-{c.id}", "mongo", "frequency", (c.id,), "runtime",
                f"Frequency max=1 for {fact.name} is enforced by a unique index over constrained roles.",
                setup=tuple(setup),
                steps=(
                    {"op": "insert_one", "collection": slug(fact.name), "document": row1, "expect": "accept"},
                    {"op": "insert_one", "collection": slug(fact.name), "document": row2, "expect": "reject"},
                ),
            ))

        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNORDERED_ROLE_GROUP):
            # Only capabilities marked native are expected to be runtime-coverable.
            groups = []
            for rid in c.role_ids:
                role = next(r for r in fact.roles if r.id == rid)
                groups.append(tuple(mongo._role_properties(model, role)))
            if not all(len(g) == 1 for g in groups):
                continue
            canonical_variants = {rid: idx for idx, rid in enumerate(c.role_ids)}
            reversed_variants = dict(canonical_variants)
            vals = list(reversed(canonical_variants.values()))
            for rid, val in zip(c.role_ids, vals):
                reversed_variants[rid] = val
            valid = _mongo_fact_doc(model, fact, canonical_variants, entity_docs)
            invalid = _mongo_fact_doc(model, fact, reversed_variants, entity_docs)
            if "_id" in invalid:
                invalid["_id"] = {"$fg_type": "objectId", "value": "000000000000000000000002"}
            cases.append(ConformanceCase(
                f"mongo-unordered-{c.id}", "mongo", "unordered_roles", (c.id,), "runtime",
                f"MongoDB $expr accepts canonical order and rejects reversed unordered-role input for {fact.name}.",
                setup=tuple(setup),
                steps=(
                    {"op": "insert_one", "collection": slug(fact.name), "document": valid, "expect": "accept"},
                    {"op": "insert_one", "collection": slug(fact.name), "document": invalid, "expect": "reject"},
                ),
            ))

        for c in model.constraints_for_fact(fact.id, ConstraintKind.RING):
            if c.ring_kind == "symmetric" and len(fact.roles) == 2:
                forward = _mongo_fact_doc(model, fact, {fact.roles[0].id: 0, fact.roles[1].id: 1}, entity_docs)
                reverse = _mongo_fact_doc(model, fact, {fact.roles[0].id: 1, fact.roles[1].id: 0}, entity_docs)
                cases.append(ConformanceCase(
                    f"mongo-gap-ring-{c.id}", "mongo", f"ring:{c.ring_kind}_not_enforced", (c.id,), "runtime",
                    f"Logical symmetry on {fact.name} is retained semantically but MongoDB does not auto-create the reverse fact.",
                    setup=tuple(setup),
                    steps=(
                        {"op": "insert_one", "collection": slug(fact.name), "document": forward, "expect": "accept"},
                        {"op": "count_documents", "collection": slug(fact.name), "filter": reverse, "expect_scalar": 0},
                    ),
                ))
    mongo_report = mongo.capability_report(model)
    for c in sorted((c for c in model.constraints.values() if c.kind == ConstraintKind.FREQUENCY and c.max_frequency != 1), key=lambda c: c.id):
        entry = next((e for e in mongo_report.entries if e.source_element == c.id), None)
        ok = entry is not None and entry.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED
        cases.append(ConformanceCase(
            f"mongo-gap-frequency-{c.id}", "mongo", "frequency_not_enforced", (c.id,), "structural",
            "MongoDB adapter explicitly reports frequency bounds above one as represented but not enforced.",
            static_pass=ok, evidence=entry.reason if entry else "missing capability entry",
        ))
    for c in sorted((c for c in model.constraints.values() if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}), key=lambda c: c.id):
        entry = next((e for e in mongo_report.entries if e.source_element == c.id), None)
        ok = entry is not None and entry.status == CapabilityStatus.REPRESENTED_NOT_ENFORCED
        cases.append(ConformanceCase(
            f"mongo-gap-{c.kind.value}-{c.id}", "mongo", f"{c.kind.value}_not_enforced", (c.id,), "structural",
            f"MongoDB adapter explicitly reports {c.kind.value} as represented but not enforced.",
            static_pass=ok, evidence=entry.reason if entry else "missing capability entry",
        ))
    return cases


def cases_json(cases: list[ConformanceCase]) -> str:
    return json.dumps([c.to_dict() for c in cases], indent=2, sort_keys=True) + "\n"




def _typedb_required_field_hints(model: Model, object_id: str):
    """Required direct/inherited field projections for constructing a live witness."""
    chain: list[str] = []
    cur = object_id
    seen: set[str] = set()
    while cur not in seen:
        seen.add(cur)
        chain.append(cur)
        sub = model.subtype_constraint(cur)
        if sub is None or sub.supertype_id is None:
            break
        cur = sub.supertype_id
    return sorted(
        (
            h for h in model.field_hints.values()
            if h.owner_object_type_id in chain and (h.required or h.identifier_component)
        ),
        key=lambda h: h.field_fact_id,
    )


def _typedb_entity_insert_statement(
    model: Model,
    object_id: str,
    var: str,
    variant: int,
    *,
    omit_field_fact_id: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> str:
    obj = model.object_types[object_id]
    if not isinstance(obj, EntityType):
        raise ValueError(f"TypeDB live witness currently requires an EntityType player, got {type(obj).__name__}")
    parts = [f"${var} isa {typedb.entity_label(obj)}"]
    for hint in _typedb_required_field_hints(model, object_id):
        if hint.field_fact_id == omit_field_fact_id:
            continue
        value = (overrides or {}).get(hint.field_fact_id, _value_for_type(model, hint.value_type_id, variant))
        if isinstance(value, dict) and "$fg_type" in value:
            value = value["value"]
        vt = model.object_types[hint.value_type_id]
        assert isinstance(vt, ValueType)
        parts.append(f"has {typedb.field_attribute_label(model, hint.field_fact_id)} {typedb._typeql_literal(value, vt.scalar_kind)}")
    return ", ".join(parts) + ";"


def _typedb_has_unrelated_required_plays(model: Model, object_id: str, *, allowed_fact_id: str | None = None, allowed_role_id: str | None = None) -> bool:
    """True when a witness entity would violate some other mandatory participation rule.

    We skip such live witnesses rather than accepting a rejection whose cause is ambiguous.
    """
    for fact in model.fact_types.values():
        if fact.id in model.field_hints:
            continue
        for role in fact.roles:
            if role.player_id != object_id:
                continue
            for c in model.constraints_for_fact(fact.id):
                if c.kind != ConstraintKind.MANDATORY or c.role_ids != (role.id,):
                    continue
                if fact.id == allowed_fact_id and role.id == allowed_role_id:
                    continue
                return True
    return False


def _typedb_invalid_value(model: Model, value_type_id: str) -> Any:
    vt = model.object_types[value_type_id]
    assert isinstance(vt, ValueType)
    constraints = model.constraints_for_value(value_type_id)
    if not constraints:
        raise ValueError(f"no value constraint for {vt.name}")
    spec = constraints[0].value_spec or {}
    if spec.get("kind") == "oneof":
        existing = list(spec.get("values", []))
        if vt.scalar_kind in {"String", "UUID"}:
            candidate = "__factgraph_outside_enum__"
            while candidate in existing:
                candidate += "x"
            return candidate
        if vt.scalar_kind == "Bool":
            for candidate in (False, True):
                if candidate not in existing:
                    return candidate
            raise ValueError("boolean oneof contains entire domain; no invalid witness exists")
        if vt.scalar_kind == "Int":
            candidate = 987654321
            while candidate in existing:
                candidate += 1
            return candidate
        if vt.scalar_kind in {"Float", "Decimal"}:
            candidate = Decimal("987654321.125") if vt.scalar_kind == "Decimal" else 987654321.125
            while candidate in existing or str(candidate) in {str(x) for x in existing}:
                candidate = candidate + (Decimal("1") if vt.scalar_kind == "Decimal" else 1.0)
            return candidate
        if vt.scalar_kind == "Date":
            return "1900-01-01" if "1900-01-01" not in existing else "2999-12-31"
        if vt.scalar_kind == "Timestamp":
            return "1900-01-01T00:00:00Z" if "1900-01-01T00:00:00Z" not in existing else "2999-12-31T23:59:59Z"
    if spec.get("kind") == "range":
        lo = spec.get("min")
        if vt.scalar_kind == "Int":
            return int(lo) - 1
        if vt.scalar_kind == "Float":
            return float(lo) - 1.0
        if vt.scalar_kind == "Decimal":
            return Decimal(str(lo)) - Decimal("1")
        if vt.scalar_kind == "Date":
            from datetime import date as _date, timedelta as _timedelta
            return (_date.fromisoformat(str(lo)) - _timedelta(days=1)).isoformat()
        if vt.scalar_kind == "Timestamp":
            from datetime import datetime as _datetime, timedelta as _timedelta
            text = str(lo).replace("Z", "+00:00")
            dt = _datetime.fromisoformat(text) - _timedelta(seconds=1)
            return dt.isoformat().replace("+00:00", "Z")
    raise ValueError(f"cannot construct invalid TypeDB witness for {vt.scalar_kind} {spec}")


def _typedb_fact_duplicate_query(model: Model, fact_id: str) -> str | None:
    """Construct an isolated duplicate-relation witness where the surrounding model permits it."""
    fact = model.fact_types[fact_id]
    statements: list[str] = []
    links: list[str] = []
    value_has: list[str] = []
    for idx, role in enumerate(sorted(fact.roles, key=lambda r: r.ordinal)):
        player = model.object_types[role.player_id]
        if isinstance(player, EntityType):
            if _typedb_has_unrelated_required_plays(model, player.id, allowed_fact_id=fact.id, allowed_role_id=role.id):
                return None
            var = f"p{idx}"
            statements.append(_typedb_entity_insert_statement(model, player.id, var, idx))
            links.append(f"{typedb.role_label(role.name)}: ${var}")
        elif isinstance(player, ValueType):
            value = _value_for_type(model, player.id, idx)
            if isinstance(value, dict) and "$fg_type" in value:
                value = value["value"]
            value_has.append(f"has {typedb.value_role_attribute_label(model, fact.id, role.id)} {typedb._typeql_literal(value, player.scalar_kind)}")
        else:
            # Objectified-relation players need recursive witness construction. Do not
            # fake it in v0.10; leave the obligation generated but not live-observed.
            return None
    rel = typedb.relation_label(model, fact.id)
    suffix = []
    if links:
        suffix.append("links (" + ", ".join(links) + ")")
    suffix.extend(value_has)
    tail = (", " + ", ".join(suffix)) if suffix else ""
    statements.append(f"$r1 isa {rel}{tail};")
    statements.append(f"$r2 isa {rel}{tail};")
    return "insert\n  " + "\n  ".join(statements)


def _typedb_native_runtime_cases(model: Model) -> list[ConformanceCase]:
    """Generate conservative live witnesses for a subset of native TypeDB claims.

    A case is emitted only when its failure can be attributed to the obligation under
    test rather than to some unrelated mandatory participation requirement.
    """
    report = typedb.capability_report(model)
    native = {
        (e.feature, e.source_element): e
        for e in report.entries
        if e.status in {CapabilityStatus.NATIVE_ENFORCED, CapabilityStatus.EMULATED_ENFORCED}
    }
    cases: list[ConformanceCase] = []

    for c in sorted(model.constraints.values(), key=lambda x: x.id):
        if (c.kind.value, c.id) not in native and not any(key[1] == c.id for key in native):
            continue

        # Generic mandatory participation: insert the player without the required fact.
        if c.kind == ConstraintKind.MANDATORY and c.fact_type_id not in model.field_hints and len(c.role_ids) == 1:
            fact = model.fact_types[c.fact_type_id]
            role = next((r for r in fact.roles if r.id == c.role_ids[0]), None)
            if role is None:
                continue
            player = model.object_types[role.player_id]
            if not isinstance(player, EntityType):
                continue
            if _typedb_has_unrelated_required_plays(model, player.id, allowed_fact_id=fact.id, allowed_role_id=role.id):
                continue
            query = "insert " + _typedb_entity_insert_statement(model, player.id, "x", 0)
            cases.append(ConformanceCase(
                id=f"typedb-live-mandatory-{c.id}", target="typedb", feature="mandatory",
                source_elements=(c.id,), mode="runtime",
                description="Live witness: an entity that omits its mandatory TypeDB role participation must be rejected at commit.",
                steps=({"typeql": query, "expect": "reject", "transaction": "write_commit"},),
            ))
            continue

        # Required field: insert owner while omitting exactly the tested field.
        if c.kind == ConstraintKind.MANDATORY and c.fact_type_id in model.field_hints:
            hint = model.field_hints[c.fact_type_id]
            owner = model.object_types[hint.owner_object_type_id]
            if not isinstance(owner, EntityType) or _typedb_has_unrelated_required_plays(model, owner.id):
                continue
            query = "insert " + _typedb_entity_insert_statement(model, owner.id, "x", 0, omit_field_fact_id=hint.field_fact_id)
            cases.append(ConformanceCase(
                id=f"typedb-live-required-field-{c.id}", target="typedb", feature="mandatory",
                source_elements=(c.id,), mode="runtime",
                description="Live witness: an owner missing a required field must be rejected at commit.",
                steps=({"typeql": query, "expect": "reject", "transaction": "write_commit"},),
            ))
            continue

        # Single-field preferred identifier: two entities with the same @key.
        if c.kind == ConstraintKind.PREFERRED_IDENTIFIER and len(c.field_fact_ids) == 1 and c.object_type_id:
            owner = model.object_types[c.object_type_id]
            if not isinstance(owner, EntityType) or _typedb_has_unrelated_required_plays(model, owner.id):
                continue
            fid = c.field_fact_ids[0]
            hint = model.field_hints.get(fid)
            if hint is None:
                continue
            shared = _value_for_type(model, hint.value_type_id, 0)
            q = "insert\n  " + _typedb_entity_insert_statement(model, owner.id, "a", 0, overrides={fid: shared}) + "\n  " + _typedb_entity_insert_statement(model, owner.id, "b", 1, overrides={fid: shared})
            cases.append(ConformanceCase(
                id=f"typedb-live-identifier-{c.id}", target="typedb", feature="preferred_identifier",
                source_elements=(c.id,), mode="runtime",
                description="Live witness: two distinct entities sharing a TypeDB @key value must be rejected at commit.",
                steps=({"typeql": q, "expect": "reject", "transaction": "write_commit"},),
            ))
            continue

        # Value constraints: use a field occurrence on an entity so the invalid value
        # reaches a concrete target attribute annotated with @values/@range.
        if c.kind == ConstraintKind.VALUE and c.object_type_id:
            occurrences = [h for h in model.field_hints.values() if h.value_type_id == c.object_type_id]
            chosen = None
            for hint in sorted(occurrences, key=lambda h: h.field_fact_id):
                owner = model.object_types[hint.owner_object_type_id]
                if isinstance(owner, EntityType) and not _typedb_has_unrelated_required_plays(model, owner.id):
                    chosen = (hint, owner)
                    break
            if chosen is None:
                continue
            hint, owner = chosen
            try:
                invalid = _typedb_invalid_value(model, c.object_type_id)
            except ValueError:
                continue
            query = "insert " + _typedb_entity_insert_statement(model, owner.id, "x", 0, overrides={hint.field_fact_id: invalid})
            # Optional fields are not emitted by _typedb_entity_insert_statement unless
            # required, so append the tested optional field explicitly when needed.
            if not (hint.required or hint.identifier_component):
                vt = model.object_types[hint.value_type_id]
                assert isinstance(vt, ValueType)
                literal = typedb._typeql_literal(invalid, vt.scalar_kind)
                query = query.rstrip(";") + f", has {typedb.field_attribute_label(model, hint.field_fact_id)} {literal};"
            cases.append(ConformanceCase(
                id=f"typedb-live-value-{c.id}", target="typedb", feature="value",
                source_elements=(c.id,), mode="runtime",
                description="Live witness: a value outside the emitted TypeDB @values/@range domain must be rejected at commit.",
                steps=({"typeql": query, "expect": "reject", "transaction": "write_commit"},),
            ))
    return cases

def typedb_cases(model: Model) -> list[ConformanceCase]:
    """Structural conformance for TypeDB claims plus explicit witness handoffs for known gaps.

    These cases intentionally do not pretend that emitted TypeQL was executed. Live TypeDB
    observation is a separate evidence layer handled by the live adapter.
    """
    plan = typedb.build_plan(model)
    schema = typedb.emit_schema(model)
    report = typedb.capability_report(model)
    attr_by_source = {a.source_element: a for a in plan.attributes}
    obj_by_source = {o.source_element: o for o in plan.objects}
    caps_by_source: dict[str, list] = {}
    for obj in plan.objects:
        for cap in obj.capabilities:
            caps_by_source.setdefault(cap.source_element, []).append(cap)
        for role_name, anns, source in obj.relates:
            caps_by_source.setdefault(source, []).append(("relates", role_name, anns, obj.name))

    cases: list[ConformanceCase] = []
    for entry in sorted(report.entries, key=lambda e: (e.source_element, e.feature)):
        if entry.status not in {CapabilityStatus.NATIVE_ENFORCED, CapabilityStatus.EMULATED_ENFORCED}:
            continue
        passed = False
        evidence = "target mapping not found"
        source = entry.source_element
        if entry.feature == "field_projection":
            attr = attr_by_source.get(source)
            mapped = caps_by_source.get(source, [])
            passed = attr is not None and bool(mapped) and attr.name in schema
            evidence = f"attribute {attr.name if attr else '<missing>'} and owner capability are present"
        elif entry.feature == "value":
            c = model.constraints[source]
            occurrences = [a for a in plan.attributes if a.source_value_type_id == c.object_type_id]
            spec = c.value_spec or {}
            needle = "@values(" if spec.get("kind") == "oneof" else "@range(" if spec.get("kind") == "range" else "@"
            passed = bool(occurrences) and all(any(a.startswith(needle) for a in x.annotations) for x in occurrences)
            evidence = f"{len(occurrences)} projected value occurrence(s) carry {needle.rstrip('(')}"
        elif entry.feature == "subtype":
            c = model.constraints[source]
            sub = obj_by_source.get(c.subtype_id)
            sup = obj_by_source.get(c.supertype_id)
            passed = sub is not None and sup is not None and sub.supertype == sup.name
            evidence = f"{sub.name if sub else '<missing>'} sub {sup.name if sup else '<missing>'}"
        elif entry.feature in {"preferred_identifier", "mandatory", "uniqueness", "frequency"}:
            c = model.constraints[source]
            related: list = []
            if c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
                for fid in c.field_fact_ids:
                    related.extend(caps_by_source.get(fid, []))
            elif c.fact_type_id in model.field_hints:
                related.extend(caps_by_source.get(c.fact_type_id, []))
            else:
                for rid in c.role_ids:
                    related.extend(caps_by_source.get(rid, []))
            text = json.dumps([getattr(x, "annotations", x) for x in related], sort_keys=True)
            if entry.feature == "preferred_identifier":
                passed = "@key" in text
                evidence = "single identifier ownership contains @key"
            elif entry.feature == "mandatory":
                passed = "@key" in text or "@card(1)" in text or "@card(1.." in text
                evidence = "mapped ownership/plays capability carries a lower-bound-one annotation"
            elif entry.feature == "uniqueness":
                # Field single-valuedness may be @card(0..1)/@card(1)/@key; role uniqueness is @card(0..1) or @unique.
                passed = any(x in text for x in ("@key", "@unique", "@card(0..1)", "@card(1)"))
                evidence = "mapped capability carries a max-one/unique annotation"
            else:
                passed = "@card(0.." in text or "@unique" in text
                evidence = "mapped role/value capability carries the supported frequency upper bound"
        else:
            # Native claim categories should be explicit above. Fail closed if a new one appears.
            passed = False
            evidence = f"no TypeDB structural checker implemented for native claim {entry.feature}"
        cases.append(ConformanceCase(
            id=f"typedb-structure-{entry.feature}-{source}",
            target="typedb",
            feature=entry.feature,
            source_elements=(source,),
            mode="structural",
            description=f"TypeDB structural mapping backs the {entry.feature} capability claim.",
            static_pass=passed,
            evidence=evidence,
        ))

    # The most important TypeDB gap gets an actual TypeQL witness whenever the
    # surrounding model can be instantiated without introducing unrelated failures.
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        query = _typedb_fact_duplicate_query(model, fact.id)
        if query is None:
            continue
        cases.append(ConformanceCase(
            id=f"typedb-gap-fact-set-{fact.id}",
            target="typedb",
            feature="fact_type",
            source_elements=(fact.id,),
            mode="runtime",
            description="Gap probe: insert two distinct TypeDB relation variables with the same conceptual role/value tuple; successful commit demonstrates that Factgraph tuple-set identity is not natively enforced.",
            steps=({"typeql": query, "expect": "accept", "transaction": "write_commit"},),
        ))

    cases.extend(_typedb_native_runtime_cases(model))
    return sorted(cases, key=lambda c: c.id)

def static_results(cases: Iterable[ConformanceCase]) -> dict[str, Any]:
    rows = []
    for c in cases:
        if c.mode != "structural":
            continue
        rows.append({
            "case_id": c.id,
            "target": c.target,
            "feature": c.feature,
            "passed": bool(c.static_pass),
            "evidence": c.evidence,
        })
    return {
        "all_passed": all(r["passed"] for r in rows),
        "count": len(rows),
        "results": rows,
    }


def coverage_report(
    model: Model,
    pg_cases: list[ConformanceCase] | None = None,
    mongo_case_list: list[ConformanceCase] | None = None,
    typedb_case_list: list[ConformanceCase] | None = None,
) -> dict[str, Any]:
    pg_cases = postgres_cases(model) if pg_cases is None else pg_cases
    mongo_case_list = mongo_cases(model) if mongo_case_list is None else mongo_case_list
    typedb_case_list = typedb_cases(model) if typedb_case_list is None else typedb_case_list
    cases_by_target = {"postgres": pg_cases, "mongo": mongo_case_list, "typedb": typedb_case_list}
    reports = {
        "postgres": postgres.capability_report(model),
        "mongo": mongo.capability_report(model),
        "typedb": typedb.capability_report(model),
    }
    target_rows: dict[str, Any] = {}
    all_uncovered: list[dict[str, Any]] = []
    for target, report in reports.items():
        cases = cases_by_target[target]
        native = [e for e in report.entries if e.status in {CapabilityStatus.NATIVE_ENFORCED, CapabilityStatus.EMULATED_ENFORCED}]
        rows = []
        for entry in native:
            matching = [c.id for c in cases if entry.source_element in c.source_elements]
            row = {
                "feature": entry.feature,
                "source_element": entry.source_element,
                "status": entry.status.value,
                "covered": bool(matching),
                "case_ids": matching,
            }
            rows.append(row)
            if not matching:
                all_uncovered.append({"target": target, **row})
        target_rows[target] = {
            "native_or_emulated_capabilities": len(native),
            "covered": sum(1 for r in rows if r["covered"]),
            "entries": rows,
        }
    return {
        "model": model.name,
        "complete": not all_uncovered,
        "targets": target_rows,
        "uncovered_native_or_emulated": all_uncovered,
        "policy": "Every capability claimed native_enforced or emulated_enforced must have at least one structural or runtime conformance case.",
    }


def bundle(model: Model) -> dict[str, Any]:
    pg = postgres_cases(model)
    mg = mongo_cases(model)
    td = typedb_cases(model)
    structural = static_results([*pg, *mg, *td])
    coverage = coverage_report(model, pg, mg, td)
    return {
        "postgres_cases": pg,
        "mongo_cases": mg,
        "typedb_cases": td,
        "static": structural,
        "coverage": coverage,
    }

