from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from ..diff import ChangeSafety, MigrationHints, align_models, semantic_diff
from ..ids import slug
from ..model import EntityType, Model
from ..targets import postgres as pg
from .base import TargetMigrationOperation, TargetMigrationPlan


_PHASE_ORDER = {
    "rename": 10,
    "create": 20,
    "shape": 30,
    "constraints": 40,
    "foreign_keys": 50,
    "cleanup": 90,
    "manual": 95,
}


def _oid(*parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":")).encode()
    return "pgmig:" + hashlib.sha256(raw).hexdigest()[:16]


def _operation(
    phase: str,
    kind: str,
    safety: ChangeSafety,
    automatic: bool,
    description: str,
    *,
    target: str | None = None,
    command: str | None = None,
    preflight: str | None = None,
    rollback: str | None = None,
    notes: tuple[str, ...] = (),
) -> TargetMigrationOperation:
    return TargetMigrationOperation(
        _oid(phase, kind, target, description, command, preflight),
        phase,
        kind,
        safety,
        automatic,
        description,
        target,
        command,
        preflight,
        rollback,
        notes,
    )


def _table_map(before: Model, after: Model, hints: MigrationHints) -> dict[str, str]:
    alignment = align_models(before, after, hints)
    bp = {t.source_name: t for t in pg.build_plan(before).tables}
    ap = {t.source_name: t for t in pg.build_plan(after).tables}
    out: dict[str, str] = {}
    for source, table in bp.items():
        if table.conceptual_kind in {"entity", "subtype_entity"}:
            mapped = alignment.object_types.get(source)
        else:
            mapped = alignment.facts.get(source)
        if mapped is not None and mapped in ap:
            out[table.name] = ap[mapped].name
    return out


def _field_hint_map(before: Model, after: Model, hints: MigrationHints):
    alignment = align_models(before, after, hints)
    return alignment.fields


def _column_map_for_table(
    before: Model,
    after: Model,
    old_table: pg.PgTable,
    new_table: pg.PgTable,
    hints: MigrationHints,
) -> dict[str, str]:
    alignment = align_models(before, after, hints)
    out: dict[str, str] = {}
    old_cols = {c.name for c in old_table.columns}
    new_cols = {c.name for c in new_table.columns}
    for name in sorted(old_cols & new_cols):
        out[name] = name

    # Entity fields, including preferred identifier components.
    if old_table.conceptual_kind in {"entity", "subtype_entity"} and new_table.conceptual_kind in {"entity", "subtype_entity"}:
        old_entity = before.object_type_by_name(old_table.source_name)
        new_entity = after.object_type_by_name(new_table.source_name)
        if isinstance(old_entity, EntityType) and isinstance(new_entity, EntityType):
            for (old_owner, old_field), (new_owner, new_field) in alignment.fields.items():
                if old_owner == old_entity.name and new_owner == new_entity.name:
                    oc, nc = slug(old_field), slug(new_field)
                    if oc in old_cols and nc in new_cols:
                        out[oc] = nc
            # Subtype tables carry inherited identifier columns. Align effective
            # key components by their conceptual field mapping when possible.
            old_ids = pg._identifier_hints(before, old_entity.id)
            new_ids = pg._identifier_hints(after, new_entity.id)
            if len(old_ids) == len(new_ids):
                for oh, nh in zip(old_ids, new_ids):
                    ok = (before.object_types[oh.owner_object_type_id].name, oh.field_name)
                    nk = alignment.fields.get(ok)
                    if nk is not None and nk[1] == nh.field_name:
                        oc, nc = slug(oh.field_name), slug(nh.field_name)
                        if oc in old_cols and nc in new_cols:
                            out[oc] = nc

    # Fact role columns and objectified relationship fields.
    if old_table.conceptual_kind in {"fact", "objectified_fact"} and new_table.conceptual_kind in {"fact", "objectified_fact"}:
        old_fact = before.fact_by_name(old_table.source_name)
        new_fact = after.fact_by_name(new_table.source_name)
        rmap = alignment.roles.get(old_fact.name, {})
        old_roles = {r.name: r for r in old_fact.roles}
        new_roles = {r.name: r for r in new_fact.roles}
        for old_role_name, new_role_name in rmap.items():
            orole, nrole = old_roles[old_role_name], new_roles[new_role_name]
            ocols = pg._role_columns(before, orole)
            ncols = pg._role_columns(after, nrole)
            if len(ocols) == len(ncols):
                for (oc, _), (nc, _) in zip(ocols, ncols):
                    if oc in old_cols and nc in new_cols:
                        out[oc] = nc
        bobj = before.objectification_for_fact(old_fact.id)
        aobj = after.objectification_for_fact(new_fact.id)
        if bobj is not None and aobj is not None:
            for (old_owner, old_field), (new_owner, new_field) in alignment.fields.items():
                if old_owner == bobj.name and new_owner == aobj.name:
                    oc, nc = slug(old_field), slug(new_field)
                    if oc in old_cols and nc in new_cols:
                        out[oc] = nc
        if "id" in old_cols and "id" in new_cols:
            out["id"] = "id"
    return out


def _map_cols(cols: Iterable[str], mapping: dict[str, str]) -> tuple[str, ...]:
    return tuple(mapping.get(c, c) for c in cols)


def _map_expr(expr: str, mapping: dict[str, str]) -> str:
    out = expr
    for old in sorted(mapping, key=lambda x: (-len(x), x)):
        new = mapping[old]
        if old == new:
            continue
        out = re.sub(rf"\b{re.escape(old)}\b", new, out)
    return out


def _create_table_sql(table: pg.PgTable) -> str:
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
    return f"CREATE TABLE {table.name} (\n" + ",\n".join(defs) + "\n);"


def _fk_sql(table_name: str, fk: pg.PgForeignKey) -> str:
    return (
        f"ALTER TABLE {table_name} ADD FOREIGN KEY ({', '.join(fk.columns)}) "
        f"REFERENCES {fk.target_table} ({', '.join(fk.target_columns)});"
    )


def _fk_key(fk: pg.PgForeignKey) -> tuple:
    return (fk.columns, fk.target_table, fk.target_columns)


def _fk_preflight(table: str, fk: pg.PgForeignKey) -> str:
    joins = " AND ".join(f"s.{sc} = t.{tc}" for sc, tc in zip(fk.columns, fk.target_columns))
    nonnull = " AND ".join(f"s.{c} IS NOT NULL" for c in fk.columns)
    missing = f"t.{fk.target_columns[0]} IS NULL"
    return (
        f"SELECT COUNT(*) AS violations FROM {table} s "
        f"LEFT JOIN {fk.target_table} t ON {joins} WHERE {nonnull} AND {missing};"
    )


def _unique_preflight(table: str, cols: tuple[str, ...]) -> str:
    names = ", ".join(cols)
    return f"SELECT {names}, COUNT(*) FROM {table} GROUP BY {names} HAVING COUNT(*) > 1;"


def build_plan(before: Model, after: Model, hints: MigrationHints | None = None) -> TargetMigrationPlan:
    hints = hints or MigrationHints.empty()
    alignment = align_models(before, after, hints)
    before_plan, after_plan = pg.build_plan(before), pg.build_plan(after)
    bt = {t.name: t for t in before_plan.tables}
    at = {t.name: t for t in after_plan.tables}
    tmap = _table_map(before, after, hints)
    mapped_old = set(tmap)
    mapped_new = set(tmap.values())
    ops: list[TargetMigrationOperation] = []
    warnings: list[str] = []

    # New tables are empty, so their own NOT NULL/check/unique constraints are safe.
    for name in sorted(set(at) - mapped_new):
        table = at[name]
        ops.append(_operation("create", "create_table", ChangeSafety.SAFE, True,
            f"create new table {name}", target=name, command=_create_table_sql(table), rollback=f"DROP TABLE {name};"))
        for fk in table.foreign_keys:
            ops.append(_operation("foreign_keys", "add_foreign_key", ChangeSafety.SAFE, True,
                f"add foreign key on new table {name} to {fk.target_table}", target=name, command=_fk_sql(name, fk)))

    # Dropped tables are destructive and never automatic by default.
    for name in sorted(set(bt) - mapped_old):
        ops.append(_operation("cleanup", "drop_table", ChangeSafety.DESTRUCTIVE, False,
            f"drop table {name} and all remaining data", target=name, command=f"DROP TABLE {name};",
            preflight=f"SELECT COUNT(*) AS rows_to_drop FROM {name};",
            notes=("take a backup or migrate retained data before enabling this operation",)))

    # Aligned tables, including explicitly hinted renames.
    for old_name, new_name in sorted(tmap.items()):
        old, new = bt[old_name], at[new_name]
        working_name = new_name
        if old_name != new_name:
            ops.append(_operation("rename", "rename_table", ChangeSafety.SAFE, True,
                f"rename table {old_name} to {new_name}", target=old_name,
                command=f"ALTER TABLE {old_name} RENAME TO {new_name};",
                rollback=f"ALTER TABLE {new_name} RENAME TO {old_name};"))

        cmap = _column_map_for_table(before, after, old, new, hints)
        old_cols = {c.name: c for c in old.columns}
        new_cols = {c.name: c for c in new.columns}
        mapped_old_cols = set(cmap)
        mapped_new_cols = set(cmap.values())

        for oc, nc in sorted(cmap.items()):
            bcol, acol = old_cols[oc], new_cols[nc]
            if oc != nc:
                ops.append(_operation("rename", "rename_column", ChangeSafety.SAFE, True,
                    f"rename column {working_name}.{oc} to {nc}", target=working_name,
                    command=f"ALTER TABLE {working_name} RENAME COLUMN {oc} TO {nc};",
                    rollback=f"ALTER TABLE {working_name} RENAME COLUMN {nc} TO {oc};"))
            if bcol.sql_type != acol.sql_type:
                ops.append(_operation("manual", "change_column_type", ChangeSafety.MANUAL, False,
                    f"change type of {working_name}.{nc} from {bcol.sql_type} to {acol.sql_type}", target=working_name,
                    command=f"ALTER TABLE {working_name} ALTER COLUMN {nc} TYPE {acol.sql_type};",
                    preflight=f"-- define a USING expression and verify conversion for {working_name}.{nc}",
                    notes=("type conversion is intentionally not invented by the planner",)))
            if bcol.nullable != acol.nullable:
                if bcol.nullable and not acol.nullable:
                    ops.append(_operation("constraints", "set_not_null", ChangeSafety.REQUIRES_DATA_CHECK, False,
                        f"make {working_name}.{nc} required", target=working_name,
                        command=f"ALTER TABLE {working_name} ALTER COLUMN {nc} SET NOT NULL;",
                        preflight=f"SELECT COUNT(*) AS null_rows FROM {working_name} WHERE {nc} IS NULL;",
                        rollback=f"ALTER TABLE {working_name} ALTER COLUMN {nc} DROP NOT NULL;"))
                else:
                    ops.append(_operation("shape", "drop_not_null", ChangeSafety.SAFE, True,
                        f"allow nulls in {working_name}.{nc}", target=working_name,
                        command=f"ALTER TABLE {working_name} ALTER COLUMN {nc} DROP NOT NULL;"))

        for nc in sorted(set(new_cols) - mapped_new_cols):
            col = new_cols[nc]
            if col.nullable:
                ops.append(_operation("shape", "add_column", ChangeSafety.SAFE, True,
                    f"add optional column {working_name}.{nc}", target=working_name,
                    command=f"ALTER TABLE {working_name} ADD COLUMN {nc} {col.sql_type};",
                    rollback=f"ALTER TABLE {working_name} DROP COLUMN {nc};"))
            else:
                # Stage a required column as nullable, leaving backfill + NOT NULL gated.
                ops.append(_operation("shape", "add_column_staged", ChangeSafety.SAFE, True,
                    f"stage new required column {working_name}.{nc} as nullable before backfill", target=working_name,
                    command=f"ALTER TABLE {working_name} ADD COLUMN {nc} {col.sql_type};",
                    rollback=f"ALTER TABLE {working_name} DROP COLUMN {nc};",
                    notes=("this intermediate schema is intentionally weaker until the gated NOT NULL step is completed",)))
                ops.append(_operation("constraints", "set_not_null_after_backfill", ChangeSafety.REQUIRES_DATA_CHECK, False,
                    f"enforce requiredness for new column {working_name}.{nc} after backfill", target=working_name,
                    command=f"ALTER TABLE {working_name} ALTER COLUMN {nc} SET NOT NULL;",
                    preflight=f"SELECT COUNT(*) AS null_rows FROM {working_name} WHERE {nc} IS NULL;",
                    notes=("supply an application-specific backfill before enabling this operation",)))

        for oc in sorted(set(old_cols) - mapped_old_cols):
            ops.append(_operation("cleanup", "drop_column", ChangeSafety.DESTRUCTIVE, False,
                f"drop column {working_name}.{oc} and its data", target=working_name,
                command=f"ALTER TABLE {working_name} DROP COLUMN {oc};",
                preflight=f"SELECT COUNT(*) AS non_null_values FROM {working_name} WHERE {oc} IS NOT NULL;"))

        # Key shape changes are not guessed because the canonical base emitter uses unnamed constraints.
        mapped_pk = _map_cols(old.primary_key, cmap)
        if mapped_pk != new.primary_key:
            ops.append(_operation("manual", "change_primary_key", ChangeSafety.MANUAL, False,
                f"primary key shape of {working_name} changes", target=working_name,
                preflight=f"-- before: PRIMARY KEY ({', '.join(mapped_pk)})\n-- after: PRIMARY KEY ({', '.join(new.primary_key)})",
                notes=("existing v0.3/v0.4 canonical schemas may have unnamed primary-key constraints; explicit identity migration is required",)))

        old_uniques = {_map_cols(u, cmap) for u in old.uniques}
        new_uniques = set(new.uniques)
        for u in sorted(new_uniques - old_uniques):
            ops.append(_operation("constraints", "add_unique", ChangeSafety.REQUIRES_DATA_CHECK, False,
                f"add uniqueness on {working_name}({', '.join(u)})", target=working_name,
                command=f"ALTER TABLE {working_name} ADD UNIQUE ({', '.join(u)});",
                preflight=_unique_preflight(working_name, u)))
        for u in sorted(old_uniques - new_uniques):
            ops.append(_operation("manual", "drop_unique", ChangeSafety.MANUAL, False,
                f"remove uniqueness on {working_name}({', '.join(u)})", target=working_name,
                preflight=f"-- locate the existing UNIQUE constraint for ({', '.join(u)}) in pg_constraint before dropping it",
                notes=("canonical schemas historically used unnamed UNIQUE constraints, so the planner will not guess a constraint name",)))

        old_checks = {_map_expr(c, cmap) for c in old.checks}
        new_checks = set(new.checks)
        for check in sorted(new_checks - old_checks):
            ops.append(_operation("constraints", "add_check", ChangeSafety.REQUIRES_DATA_CHECK, False,
                f"add CHECK on {working_name}: {check}", target=working_name,
                command=f"ALTER TABLE {working_name} ADD CHECK ({check});",
                preflight=f"SELECT COUNT(*) AS violations FROM {working_name} WHERE NOT ({check});"))
        for check in sorted(old_checks - new_checks):
            ops.append(_operation("manual", "drop_check", ChangeSafety.MANUAL, False,
                f"remove CHECK from {working_name}: {check}", target=working_name,
                preflight="-- locate the existing CHECK constraint in pg_constraint before dropping it",
                notes=("canonical schemas historically used unnamed CHECK constraints",)))

        def mapped_fk(fk: pg.PgForeignKey) -> pg.PgForeignKey:
            target_table = tmap.get(fk.target_table, fk.target_table)
            target_cmap: dict[str, str] = {}
            if fk.target_table in tmap:
                old_target = bt[fk.target_table]
                new_target = at[tmap[fk.target_table]]
                target_cmap = _column_map_for_table(before, after, old_target, new_target, hints)
            return pg.PgForeignKey(_map_cols(fk.columns, cmap), target_table, _map_cols(fk.target_columns, target_cmap))

        old_fks = {_fk_key(mapped_fk(fk)): mapped_fk(fk) for fk in old.foreign_keys}
        new_fks = {_fk_key(fk): fk for fk in new.foreign_keys}
        for key in sorted(set(new_fks) - set(old_fks), key=str):
            fk = new_fks[key]
            ops.append(_operation("foreign_keys", "add_foreign_key", ChangeSafety.REQUIRES_DATA_CHECK, False,
                f"add foreign key on {working_name}({', '.join(fk.columns)}) to {fk.target_table}", target=working_name,
                command=_fk_sql(working_name, fk), preflight=_fk_preflight(working_name, fk)))
        for key in sorted(set(old_fks) - set(new_fks), key=str):
            fk = old_fks[key]
            ops.append(_operation("manual", "drop_foreign_key", ChangeSafety.MANUAL, False,
                f"remove foreign key on {working_name}({', '.join(fk.columns)}) to {fk.target_table}", target=working_name,
                preflight="-- locate the existing FOREIGN KEY constraint in pg_constraint before dropping it",
                notes=("canonical schemas historically used unnamed FOREIGN KEY constraints",)))

    # If hints mapped elements but target names did not change, that is fine; if
    # semantic changes are not visible structurally, surface that as a warning.
    if hints.roles:
        warnings.append("role rename hints are translated to column renames only when the before/after canonical role column shapes have matching arity")
    if any(c.kind.value in {"add_subtype", "drop_subtype", "change_subtype"} for c in semantic_diff(before, after, hints).changes):
        warnings.append("subtype changes alter identity/table shape and remain manual even when some structural operations can be previewed")

    kind_rank = {"rename_table": 0, "rename_column": 1}
    ops.sort(key=lambda op: (_PHASE_ORDER.get(op.phase, 50), kind_rank.get(op.kind, 10), op.target_object or "", op.kind, op.id))
    return TargetMigrationPlan("postgres", before.name, after.name, ops, warnings)


def _render_script(plan: TargetMigrationPlan, *, include_risky: bool, include_destructive: bool) -> str:
    lines = [
        "-- generated by factgraph v0.5 semantic migration planner",
        f"-- {plan.before_model} -> {plan.after_model}",
        "-- SAFE operations are executable. Gated/manual operations remain comments unless the selected preview enables them.",
        "",
        "BEGIN;",
        "",
    ]
    for op in plan.operations:
        lines.append(f"-- [{op.safety.value}] {op.description}")
        if op.preflight:
            for ln in op.preflight.splitlines():
                lines.append("-- preflight: " + ln)
        permitted = op.automatic
        if op.safety == ChangeSafety.REQUIRES_DATA_CHECK and include_risky and op.command:
            permitted = True
        if op.safety == ChangeSafety.DESTRUCTIVE and include_destructive and op.command:
            permitted = True
        if op.safety == ChangeSafety.MANUAL:
            permitted = False
        if op.command:
            if permitted:
                lines.extend(op.command.splitlines())
            else:
                for ln in op.command.splitlines():
                    lines.append("-- BLOCKED: " + ln)
        else:
            lines.append("-- no automatic SQL emitted")
        lines.append("")
    lines += ["COMMIT;", ""]
    return "\n".join(lines)


def emit_safe_sql(plan: TargetMigrationPlan) -> str:
    return _render_script(plan, include_risky=False, include_destructive=False)


def emit_risky_preview_sql(plan: TargetMigrationPlan) -> str:
    return _render_script(plan, include_risky=True, include_destructive=False)


def emit_destructive_preview_sql(plan: TargetMigrationPlan) -> str:
    return _render_script(plan, include_risky=True, include_destructive=True)


def emit_preflight_sql(plan: TargetMigrationPlan) -> str:
    lines = [
        "-- generated preflight queries for gated factgraph PostgreSQL migration operations",
        f"-- {plan.before_model} -> {plan.after_model}",
        "",
    ]
    for op in plan.operations:
        if op.preflight:
            lines.append(f"-- {op.id}: [{op.safety.value}] {op.description}")
            lines.extend(op.preflight.splitlines())
            if not op.preflight.rstrip().endswith(";"):
                lines[-1] += ";"
            lines.append("")
    if len(lines) == 3:
        lines.append("-- no preflight queries required")
    return "\n".join(lines).rstrip() + "\n"
