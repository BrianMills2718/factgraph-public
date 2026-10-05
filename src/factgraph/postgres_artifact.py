from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .model import ConstraintKind, EntityType, Model, ObjectifiedFactType
from .ids import artifact_filename_token, slug
from .acceptance_witness import AcceptanceProbe, synthesize_acceptance_probes
from .shared_witness_execution import (
    SharedWitnessCase,
    _evaluate,
    _split_sql,
    build_cases,
    case_plan_sha256,
    model_semantic_sha256,
)
from .targets import postgres
from . import witness_lowering as lowering
from .witness_lowering import LoweringProgram


FORMAT_V1 = "factgraph-postgres-artifact-mapping-v1"
FORMAT_V2 = "factgraph-postgres-artifact-mapping-v2"
FORMAT = "factgraph-postgres-artifact-mapping-v3"
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOWER_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
_INSERT = re.compile(
    r"^INSERT INTO (?P<table>[A-Za-z_][A-Za-z0-9_]*) \((?P<cols>[^)]*)\)"
    r"(?P<override> OVERRIDING SYSTEM VALUE)? VALUES \((?P<vals>.*)\);$",
    re.S,
)
_DEFAULT_INSERT = re.compile(r"^INSERT INTO (?P<table>[A-Za-z_][A-Za-z0-9_]*) DEFAULT VALUES;$")


@dataclass(frozen=True)
class ExternalAcceptanceCase:
    obligation_id: str
    kind: str
    probe_id: str
    label: str
    source_probe: AcceptanceProbe
    program: LoweringProgram

    def to_dict(self, model: Model) -> dict[str, Any]:
        return {
            "format": "factgraph-external-postgres-acceptance-case-v1",
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "probe_id": self.probe_id,
            "label": self.label,
            "source_probe": self.source_probe.to_dict(model),
            "program": self.program.to_dict(),
        }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sql_ident(name: str) -> str:
    """Render an exact physical PostgreSQL identifier from a mapping manifest.

    Mapping v2 stores the catalog identifier *without SQL quotes*. Lower-case
    simple names are emitted bare; mixed-case names are quoted so tools such as
    Factum that intentionally generate case-sensitive identifiers can be audited
    without rewriting their DDL.
    """
    if _LOWER_IDENT.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def mapping_template(model: Model, artifact_sql: str) -> dict[str, Any]:
    entities: dict[str, Any] = {}
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        field_columns: dict[str, str] = {}
        effective_ids = lowering._identifier_hints(model, entity.id)
        for hint in effective_ids:
            field_columns[hint.field_fact_id] = slug(hint.field_name)
        for hint in lowering._hints_for_owner(model, entity.id):
            field_columns[hint.field_fact_id] = slug(hint.field_name)
        entities[entity.id] = {
            "source_name": entity.name,
            "table": slug(entity.name),
            "field_columns": field_columns,
            "synthetic_id_column": None if effective_ids else "id",
        }

    facts: dict[str, Any] = {}
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        obj = model.objectification_for_fact(fact.id)
        fields: dict[str, str] = {}
        if obj is not None:
            for hint in lowering._hints_for_owner(model, obj.id):
                fields[hint.field_fact_id] = slug(hint.field_name)
        facts[fact.id] = {
            "source_name": fact.name,
            "storage_mode": "table",
            "anchor_role_id": None,
            "table": slug(fact.name),
            "role_columns": {
                role.id: [name for name, _typ in postgres._role_columns(model, role)]
                for role in sorted(fact.roles, key=lambda r: r.ordinal)
            },
            "field_columns": fields,
            "objectified_id_column": "id" if obj is not None else None,
        }

    return {
        "format": FORMAT,
        "source_model": model.name,
        "source_model_id": model.id,
        "source_model_semantic_sha256": model_semantic_sha256(model),
        "artifact_sha256": sha256_text(artifact_sql),
        "layout_contract": "one table per entity; source facts may use a distinct table or, for bounded non-objectified binary facts, be explicitly absorbed into the table of one entity role; scalar field facts map to columns; role references retain canonical role-column arity but names may differ",
        "identifier_contract": "mapping values are exact PostgreSQL catalog identifier names without SQL quote characters; lower-case names render unquoted, mixed-case names render quoted; mapping v3 permits only explicitly declared binary fact absorption and still does not infer arbitrary denormalized/shared-table layouts",
        "entities": entities,
        "facts": facts,
    }


def _require_ident(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        errors.append(f"{label} must be a simple unquoted PostgreSQL identifier")


def _fact_storage_mode(mapping: dict[str, Any], fact_id: str) -> str:
    row = mapping.get("facts", {}).get(fact_id, {})
    mode = row.get("storage_mode", "table") if isinstance(row, dict) else "table"
    return mode if isinstance(mode, str) else "table"


def _entity_identifier_physical_columns(model: Model, mapping: dict[str, Any], entity: EntityType) -> list[str]:
    row = mapping["entities"][entity.id]
    hints = lowering._identifier_hints(model, entity.id)
    if hints:
        return [row["field_columns"][hint.field_fact_id] for hint in hints]
    return [row["synthetic_id_column"]]


def validate_mapping(model: Model, artifact_sql: str, mapping: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    fmt = mapping.get("format")
    if fmt not in {FORMAT, FORMAT_V2, FORMAT_V1}:
        errors.append(f"mapping format must be {FORMAT} (or legacy {FORMAT_V2}/{FORMAT_V1})")
    elif fmt == FORMAT_V1:
        warnings.append("legacy v1 mapping accepted; v2 added exact case-sensitive PostgreSQL identifiers and v3 adds explicit binary fact absorption")
    elif fmt == FORMAT_V2:
        warnings.append("legacy v2 mapping accepted; v3 adds explicit binary fact absorption")
    if mapping.get("source_model_semantic_sha256") != model_semantic_sha256(model):
        errors.append("source_model_semantic_sha256 does not match the normalized source model")
    if mapping.get("artifact_sha256") != sha256_text(artifact_sql):
        errors.append("artifact_sha256 does not match the supplied PostgreSQL artifact")
    entities = mapping.get("entities")
    facts = mapping.get("facts")
    if not isinstance(entities, dict):
        errors.append("entities must be an object"); entities = {}
    if not isinstance(facts, dict):
        errors.append("facts must be an object"); facts = {}

    entity_table_owner: dict[str, str] = {}
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        row = entities.get(entity.id)
        if not isinstance(row, dict):
            errors.append(f"missing entity mapping for {entity.id}"); continue
        _require_ident(row.get("table"), f"entity {entity.name} table", errors)
        if isinstance(row.get("table"), str):
            table = row["table"]
            if table in entity_table_owner:
                errors.append(f"entities {entity_table_owner[table]} and {entity.name} share target table {table}; entity-table absorption is not supported")
            else:
                entity_table_owner[table] = entity.name
        field_columns = row.get("field_columns")
        if not isinstance(field_columns, dict):
            errors.append(f"entity {entity.name} field_columns must be an object"); field_columns = {}
        expected_hints = {h.field_fact_id: h for h in lowering._identifier_hints(model, entity.id)}
        expected_hints.update({h.field_fact_id: h for h in lowering._hints_for_owner(model, entity.id)})
        for fid, hint in sorted(expected_hints.items()):
            if fid not in field_columns:
                errors.append(f"entity {entity.name} missing field mapping for {hint.field_name} ({fid})")
            else:
                _require_ident(field_columns[fid], f"entity {entity.name} field {hint.field_name}", errors)
        if not lowering._identifier_hints(model, entity.id):
            _require_ident(row.get("synthetic_id_column"), f"entity {entity.name} synthetic_id_column", errors)

    table_fact_owners: dict[str, list[str]] = {}
    absorbed_by_table: dict[str, list[str]] = {}
    absorbed_non_anchor_cols: dict[str, dict[str, str]] = {}
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        row = facts.get(fact.id)
        if not isinstance(row, dict):
            errors.append(f"missing fact mapping for {fact.id}"); continue
        mode = row.get("storage_mode", "table")
        if mode not in {"table", "absorbed"}:
            errors.append(f"fact {fact.name} storage_mode must be table or absorbed")
            mode = "table"
        if mode == "absorbed" and fmt != FORMAT:
            errors.append(f"fact {fact.name} uses absorbed storage, which requires mapping format {FORMAT}")
        _require_ident(row.get("table"), f"fact {fact.name} table", errors)
        table = row.get("table") if isinstance(row.get("table"), str) else None
        roles = row.get("role_columns")
        if not isinstance(roles, dict):
            errors.append(f"fact {fact.name} role_columns must be an object"); roles = {}
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            expected_arity = len(postgres._role_columns(model, role))
            cols = roles.get(role.id)
            if not isinstance(cols, list) or len(cols) != expected_arity:
                errors.append(f"fact {fact.name} role {role.name} requires {expected_arity} mapped column(s)")
            else:
                for idx, col in enumerate(cols):
                    _require_ident(col, f"fact {fact.name} role {role.name} column {idx}", errors)
        fields = row.get("field_columns")
        if not isinstance(fields, dict):
            errors.append(f"fact {fact.name} field_columns must be an object"); fields = {}
        obj = model.objectification_for_fact(fact.id)
        if obj is not None:
            _require_ident(row.get("objectified_id_column"), f"fact {fact.name} objectified_id_column", errors)
            for hint in lowering._hints_for_owner(model, obj.id):
                if hint.field_fact_id not in fields:
                    errors.append(f"fact {fact.name} missing objectified field mapping for {hint.field_name}")
                else:
                    _require_ident(fields[hint.field_fact_id], f"fact {fact.name} field {hint.field_name}", errors)

        if mode == "table":
            if table is not None:
                table_fact_owners.setdefault(table, []).append(fact.name)
            continue

        # v3 deliberately supports only a narrow, auditable Rmap-style shape:
        # a non-objectified binary fact represented by columns on one entity
        # role's table. No inference from SQL text is performed.
        if len(fact.roles) != 2:
            errors.append(f"absorbed fact {fact.name} must be binary")
            continue
        if obj is not None:
            errors.append(f"absorbed fact {fact.name} cannot be objectified")
        if fields:
            errors.append(f"absorbed fact {fact.name} cannot carry relationship fields")
        if row.get("objectified_id_column") not in {None, ""}:
            errors.append(f"absorbed fact {fact.name} cannot define objectified_id_column")
        anchor_id = row.get("anchor_role_id")
        anchor = next((r for r in fact.roles if r.id == anchor_id), None)
        if anchor is None:
            errors.append(f"absorbed fact {fact.name} anchor_role_id must name one of its roles")
            continue
        anchor_player = model.object_types.get(anchor.player_id)
        if not isinstance(anchor_player, EntityType):
            errors.append(f"absorbed fact {fact.name} anchor role {anchor.name} must be played by an entity type")
            continue
        entity_row = entities.get(anchor_player.id)
        if not isinstance(entity_row, dict):
            errors.append(f"absorbed fact {fact.name} anchor entity {anchor_player.name} has no entity mapping")
            continue
        if table is not None and table != entity_row.get("table"):
            errors.append(f"absorbed fact {fact.name} must use anchor entity {anchor_player.name} target table {entity_row.get('table')}")
        try:
            expected_anchor_cols = _entity_identifier_physical_columns(model, mapping, anchor_player)
        except Exception:
            expected_anchor_cols = []
        anchor_cols = roles.get(anchor.id)
        if isinstance(anchor_cols, list) and expected_anchor_cols and anchor_cols != expected_anchor_cols:
            errors.append(
                f"absorbed fact {fact.name} anchor role columns {anchor_cols} must equal anchor entity identifier columns {expected_anchor_cols}"
            )
        if table is not None:
            absorbed_by_table.setdefault(table, []).append(fact.name)
            col_owner = absorbed_non_anchor_cols.setdefault(table, {})
            for role in fact.roles:
                if role.id == anchor.id:
                    continue
                for col in roles.get(role.id, []) if isinstance(roles.get(role.id), list) else []:
                    previous = col_owner.get(col)
                    if previous is not None and previous != fact.name:
                        errors.append(
                            f"absorbed facts {previous} and {fact.name} both map non-anchor data to {table}.{col}; overlapping absorbed columns are ambiguous"
                        )
                    else:
                        col_owner[col] = fact.name

    for table, fact_names in sorted(table_fact_owners.items()):
        if table in entity_table_owner:
            errors.append(
                f"table-mode fact(s) {fact_names} share entity table {table}; use storage_mode=absorbed for the bounded binary absorption contract"
            )
        if len(fact_names) > 1:
            errors.append(f"table-mode facts {fact_names} share target table {table}; shared fact tables are not supported")
    for table in sorted(absorbed_by_table):
        if table not in entity_table_owner:
            errors.append(f"absorbed fact target table {table} is not mapped by an anchor entity")

    return {
        "format": "factgraph-postgres-artifact-mapping-validation-v3",
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "source_model_semantic_sha256": model_semantic_sha256(model),
        "artifact_sha256": sha256_text(artifact_sql),
    }

def _physical_maps(model: Model, mapping: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    table_map: dict[str, str] = {}
    columns: dict[str, dict[str, str]] = {}
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        row = mapping["entities"][entity.id]
        canonical_table = slug(entity.name)
        table_map[canonical_table] = row["table"]
        cmap: dict[str, str] = {}
        for hint in lowering._identifier_hints(model, entity.id):
            cmap[slug(hint.field_name)] = row["field_columns"][hint.field_fact_id]
        for hint in lowering._hints_for_owner(model, entity.id):
            cmap[slug(hint.field_name)] = row["field_columns"][hint.field_fact_id]
        if not lowering._identifier_hints(model, entity.id):
            cmap["id"] = row["synthetic_id_column"]
        columns[canonical_table] = cmap
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        row = mapping["facts"][fact.id]
        canonical_table = slug(fact.name)
        table_map[canonical_table] = row["table"]
        cmap: dict[str, str] = {}
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            canonical_cols = [name for name, _typ in postgres._role_columns(model, role)]
            for old, new in zip(canonical_cols, row["role_columns"][role.id]):
                cmap[old] = new
        obj = model.objectification_for_fact(fact.id)
        if obj is not None:
            cmap["id"] = row["objectified_id_column"]
            for hint in lowering._hints_for_owner(model, obj.id):
                cmap[slug(hint.field_name)] = row["field_columns"][hint.field_fact_id]
        columns[canonical_table] = cmap
    return table_map, columns


def rewrite_insert(sql: str, table_map: dict[str, str], column_maps: dict[str, dict[str, str]]) -> str:
    m = _DEFAULT_INSERT.fullmatch(sql.strip())
    if m:
        table = m.group("table")
        if table not in table_map:
            raise ValueError(f"no external table mapping for canonical table {table}")
        return f"INSERT INTO {_sql_ident(table_map[table])} DEFAULT VALUES;"
    m = _INSERT.fullmatch(sql.strip())
    if not m:
        raise ValueError(f"unsupported canonical INSERT shape for external rewrite: {sql}")
    table = m.group("table")
    if table not in table_map:
        raise ValueError(f"no external table mapping for canonical table {table}")
    cmap = column_maps.get(table, {})
    old_cols = [x.strip() for x in m.group("cols").split(",")]
    missing = [c for c in old_cols if c not in cmap]
    if missing:
        raise ValueError(f"no external column mapping for {table}: {missing}")
    cols = ", ".join(_sql_ident(cmap[c]) for c in old_cols)
    return f"INSERT INTO {_sql_ident(table_map[table])} ({cols}){m.group('override') or ''} VALUES ({m.group('vals')});"


def _split_sql_values(text: str) -> list[str]:
    """Split Factgraph-generated VALUES literals without evaluating SQL."""
    out: list[str] = []
    buf: list[str] = []
    quoted = False
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quoted:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    quoted = False
        else:
            if ch == "'":
                quoted = True
                buf.append(ch)
            elif ch == "(":
                depth += 1; buf.append(ch)
            elif ch == ")":
                depth = max(0, depth - 1); buf.append(ch)
            elif ch == "," and depth == 0:
                out.append("".join(buf).strip()); buf = []
            else:
                buf.append(ch)
        i += 1
    if buf or text.strip():
        out.append("".join(buf).strip())
    return out


def _parse_insert(sql: str) -> dict[str, Any]:
    m = _DEFAULT_INSERT.fullmatch(sql.strip())
    if m:
        return {"table": m.group("table"), "columns": [], "values": [], "override": False, "default": True}
    m = _INSERT.fullmatch(sql.strip())
    if not m:
        raise ValueError(f"unsupported canonical INSERT shape for external rewrite: {sql}")
    cols = [x.strip() for x in m.group("cols").split(",")]
    vals = _split_sql_values(m.group("vals"))
    if len(cols) != len(vals):
        raise ValueError(f"could not split generated INSERT values deterministically: {sql}")
    return {"table": m.group("table"), "columns": cols, "values": vals, "override": bool(m.group("override")), "default": False}


def _render_physical_insert(table: str, columns: list[str], values: list[str], *, override: bool = False) -> str:
    if not columns:
        return f"INSERT INTO {_sql_ident(table)} DEFAULT VALUES;"
    cols = ", ".join(_sql_ident(c) for c in columns)
    vals = ", ".join(values)
    ov = " OVERRIDING SYSTEM VALUE" if override else ""
    return f"INSERT INTO {_sql_ident(table)} ({cols}){ov} VALUES ({vals});"


def _rewrite_insert_struct(sql: str, table_map: dict[str, str], column_maps: dict[str, dict[str, str]]) -> dict[str, Any]:
    parsed = _parse_insert(sql)
    table = parsed["table"]
    if table not in table_map:
        raise ValueError(f"no external table mapping for canonical table {table}")
    cmap = column_maps.get(table, {})
    missing = [c for c in parsed["columns"] if c not in cmap]
    if missing:
        raise ValueError(f"no external column mapping for {table}: {missing}")
    cols = [cmap[c] for c in parsed["columns"]]
    return {
        **parsed,
        "canonical_table": table,
        "table": table_map[table],
        "columns": cols,
        "sql": _render_physical_insert(table_map[table], cols, parsed["values"], override=parsed["override"]),
    }


def _absorbed_fact_rows(model: Model, mapping: dict[str, Any]) -> dict[str, tuple[Any, Any, EntityType]]:
    out: dict[str, tuple[Any, Any, EntityType]] = {}
    for fact in model.fact_types.values():
        if fact.id in model.field_hints:
            continue
        row = mapping["facts"][fact.id]
        if row.get("storage_mode", "table") != "absorbed":
            continue
        anchor = next(r for r in fact.roles if r.id == row["anchor_role_id"])
        entity = model.object_types[anchor.player_id]
        assert isinstance(entity, EntityType)
        out[fact.id] = (fact, anchor, entity)
    return out


def _rewrite_operations(model: Model, operations: tuple[dict[str, Any], ...], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """Rewrite and coalesce a bounded absorbed-binary physical layout.

    Entity membership rows remain the physical row identity. The first occurrence
    of each absorbed fact for that entity merges its non-anchor columns into that
    row. A second occurrence of the *same* absorbed fact is emitted as a second
    row carrying the same entity key, so scalar/PK target semantics can reject the
    source multiplicity rather than Factgraph silently deduplicating it.
    """
    table_map, column_maps = _physical_maps(model, mapping)
    absorbed = _absorbed_fact_rows(model, mapping)
    output: list[dict[str, Any]] = []
    anchor_row_index: dict[tuple[str, str], int] = {}
    contributions: dict[int, set[str]] = {}
    pending_duplicate_occurrences: list[tuple[int, dict[str, Any], dict[str, Any], str]] = []

    def append_struct(op: dict[str, Any], struct: dict[str, Any]) -> int:
        row = dict(op)
        row["sql"] = struct["sql"]
        row["source_atoms"] = [op.get("source_atom")]
        row["_fg_struct"] = struct
        output.append(row)
        contributions[len(output) - 1] = set()
        return len(output) - 1

    for op in operations:
        struct = _rewrite_insert_struct(op["sql"], table_map, column_maps)
        atom = op.get("source_atom") or {}
        membership = atom.get("membership") if isinstance(atom, dict) else None
        if isinstance(membership, list) and len(membership) == 2:
            idx = append_struct(op, struct)
            entity_id, instance_id = membership
            if isinstance(entity_id, str) and isinstance(instance_id, str):
                anchor_row_index[(entity_id, instance_id)] = idx
            continue

        fact_id = atom.get("fact_type_id") if isinstance(atom, dict) else None
        if not isinstance(fact_id, str) or fact_id not in absorbed:
            append_struct(op, struct)
            continue

        fact, anchor, entity = absorbed[fact_id]
        source_row = atom.get("row")
        if not isinstance(source_row, list) or len(source_row) <= anchor.ordinal:
            raise ValueError(f"absorbed fact {fact.name} operation lacks source row identity")
        anchor_instance = source_row[anchor.ordinal]
        key = (entity.id, anchor_instance)
        if key not in anchor_row_index:
            raise ValueError(
                f"absorbed fact {fact.name} occurrence has no anchor entity membership row for {anchor_instance}; exact row coalescing is unavailable"
            )
        idx = anchor_row_index[key]
        base = output[idx]
        base_struct = base["_fg_struct"]
        fact_cols = dict(zip(struct["columns"], struct["values"]))
        anchor_cols = set(mapping["facts"][fact_id]["role_columns"][anchor.id])

        if fact_id in contributions[idx]:
            # Preserve multiplicity, but defer the duplicate physical row until
            # every *other* absorbed fact has had a chance to populate the anchor
            # row. Otherwise a duplicate could be rejected merely because a later
            # mandatory absorbed column was missing, which would be wrong-cause
            # evidence for the duplicated obligation.
            pending_duplicate_occurrences.append((idx, dict(op), struct, fact_id))
            continue

        current = dict(zip(base_struct["columns"], base_struct["values"]))
        for col, val in fact_cols.items():
            if col in current and current[col] != val:
                raise ValueError(
                    f"absorbed fact {fact.name} conflicts with anchor entity value for physical column {base_struct['table']}.{col}"
                )
            current[col] = val
        new_cols = list(base_struct["columns"]) + [c for c in struct["columns"] if c not in base_struct["columns"]]
        new_vals = [current[c] for c in new_cols]
        base_struct.update({
            "columns": new_cols,
            "values": new_vals,
            "sql": _render_physical_insert(base_struct["table"], new_cols, new_vals, override=base_struct["override"]),
        })
        base["sql"] = base_struct["sql"]
        base.setdefault("source_atoms", []).append(atom)
        contributions[idx].add(fact_id)

    for base_idx, op, struct, fact_id in pending_duplicate_occurrences:
        base = output[base_idx]
        base_struct = base["_fg_struct"]
        merged = dict(zip(base_struct["columns"], base_struct["values"]))
        for col, val in zip(struct["columns"], struct["values"]):
            merged[col] = val
        ordered_cols = list(base_struct["columns"]) + [c for c in struct["columns"] if c not in base_struct["columns"]]
        ordered_vals = [merged[c] for c in ordered_cols]
        clone = {
            **base_struct,
            "columns": ordered_cols,
            "values": ordered_vals,
            "sql": _render_physical_insert(base_struct["table"], ordered_cols, ordered_vals, override=base_struct["override"]),
        }
        dup = dict(op)
        dup["sql"] = clone["sql"]
        dup["source_atoms"] = list(base.get("source_atoms", [])) + [op.get("source_atom")]
        output.append(dup)

    for row in output:
        row.pop("_fg_struct", None)
    return output


def rewrite_postcondition(
    model: Model,
    condition: dict[str, Any],
    mapping: dict[str, Any],
    table_map: dict[str, str],
    column_maps: dict[str, dict[str, str]],
) -> dict[str, Any]:
    if condition.get("op") != "scalar_sql":
        raise ValueError(f"unsupported external PostgreSQL postcondition op: {condition.get('op')}")
    table = condition.get("table")
    filters = condition.get("filters")
    if not isinstance(table, str) or not isinstance(filters, list):
        raise ValueError("PostgreSQL postcondition lacks structured table/filter metadata")
    if table not in table_map:
        raise ValueError(f"no external table mapping for postcondition table {table}")
    cmap = column_maps.get(table, {})
    clauses: list[str] = []
    mapped_filters: list[dict[str, str]] = []
    for item in filters:
        old = item["column"]
        if old not in cmap:
            raise ValueError(f"no external column mapping for postcondition {table}.{old}")
        new = cmap[old]
        literal = item["literal_sql"]
        clauses.append(f"{_sql_ident(new)} = {literal}")
        mapped_filters.append({"column": new, "literal_sql": literal})

    # A canonical fact-table count becomes an existence test over non-anchor
    # columns when that fact is explicitly absorbed into its anchor entity row.
    fact = next((f for f in model.fact_types.values() if f.id not in model.field_hints and slug(f.name) == table), None)
    if fact is not None:
        fact_map = mapping["facts"][fact.id]
        if fact_map.get("storage_mode", "table") == "absorbed":
            anchor_id = fact_map["anchor_role_id"]
            non_anchor = [r for r in fact.roles if r.id != anchor_id]
            presence_cols: list[str] = []
            for role in non_anchor:
                presence_cols.extend(fact_map["role_columns"][role.id])
            clauses.extend(f"{_sql_ident(col)} IS NOT NULL" for col in presence_cols)
            out = dict(condition)
            out.update({
                "table": fact_map["table"],
                "filters": mapped_filters,
                "presence_columns": presence_cols,
                "sql": f"SELECT COUNT(*) FROM {_sql_ident(fact_map['table'])} WHERE {' AND '.join(clauses) if clauses else 'TRUE'};",
                "physical_semantics": "absorbed_fact_presence_via_non_anchor_nonnull_columns",
            })
            return out

    where = " AND ".join(clauses) if clauses else "TRUE"
    out = dict(condition)
    out.update({
        "table": table_map[table],
        "filters": mapped_filters,
        "sql": f"SELECT COUNT(*) FROM {_sql_ident(table_map[table])} WHERE {where};",
    })
    return out

def _source_poststate_required(model: Model, case: SharedWitnessCase) -> bool:
    if case.kind == "mandatory":
        constraint = model.constraints.get(case.obligation_id)
        return bool(constraint and constraint.fact_type_id not in model.field_hints)
    return case.kind in {"subset", "equality", "ring", "subtype"}


def build_external_cases(model: Model, mapping: dict[str, Any]) -> list[SharedWitnessCase]:
    table_map, column_maps = _physical_maps(model, mapping)
    rows: list[SharedWitnessCase] = []
    for case in build_cases(model, "postgres"):
        program = case.program
        if program.status == "lowered":
            limitations: list[str] = list(program.limitations)
            try:
                operations = _rewrite_operations(model, program.operations, mapping)
                mapped_program = LoweringProgram(
                    "postgres_external", "lowered", program.fidelity, tuple(operations),
                    tuple(limitations), tuple(program.notes) + (
                        "Physical identifiers and any explicit bounded binary absorption were applied through the external PostgreSQL mapping manifest.",
                    ),
                )
            except ValueError as exc:
                mapped_program = LoweringProgram(
                    "postgres_external", "unsupported", "external_mapping_cannot_rewrite_source_program",
                    (), tuple(limitations + [str(exc)]), tuple(program.notes),
                )
        else:
            mapped_program = replace(program, target="postgres_external")

        required = _source_poststate_required(model, case) and mapped_program.status == "lowered"
        mapped_post: list[dict[str, Any]] = []
        if required and case.postconditions:
            try:
                mapped_post = [rewrite_postcondition(model, c, mapping, table_map, column_maps) for c in case.postconditions]
            except ValueError as exc:
                mapped_program = replace(
                    mapped_program,
                    notes=tuple(mapped_program.notes) + (f"External post-state mapping gap: {exc}",),
                )
                mapped_post = []
        rows.append(replace(
            case,
            target="postgres_external",
            expected_write_outcome="not_asserted",
            poststate_required_for_semantic_proof=required,
            capability_status=None,
            capability_mechanism=None,
            capability_reason="External artifact audit makes no Factgraph canonical-adapter capability assumption.",
            postconditions=tuple(mapped_post),
            program=mapped_program,
        ))
    return rows

def _rewrite_program(model: Model, program: LoweringProgram, mapping: dict[str, Any]) -> LoweringProgram:
    if program.status != "lowered":
        return replace(program, target="postgres_external")
    limitations: list[str] = list(program.limitations)
    try:
        operations = _rewrite_operations(model, program.operations, mapping)
        return LoweringProgram(
            "postgres_external", "lowered", program.fidelity, tuple(operations),
            tuple(limitations), tuple(program.notes) + (
                "Physical identifiers and any explicit bounded binary absorption were applied through the external PostgreSQL mapping manifest.",
            ),
        )
    except ValueError as exc:
        return LoweringProgram(
            "postgres_external", "unsupported", "external_mapping_cannot_rewrite_source_program",
            (), tuple(limitations + [str(exc)]), tuple(program.notes),
        )

def build_external_acceptance_cases(model: Model, mapping: dict[str, Any]) -> list[ExternalAcceptanceCase]:
    """Build source-valid probes for the bounded external PostgreSQL layout.

    Source validity is established before looking at the external artifact. The
    exact same semantic population is then lowered through Factgraph's canonical
    PostgreSQL population lowering and only physical identifiers are rewritten
    through the explicit external mapping manifest.

    This deliberately includes relationship/identity probes as well as value
    boundaries. A target that rejects a source-valid probe is stronger or
    otherwise incompatible for that tested case; accepting all generated probes
    is still not a proof of full semantic equivalence.
    """
    rows: list[ExternalAcceptanceCase] = []
    obligation_specs: list[tuple[str, str]] = [
        (f"obligation:set:{fact.id}", "fact_set_semantics")
        for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id)
    ]
    obligation_specs.extend((c.id, c.kind.value) for c in sorted(model.constraints.values(), key=lambda c: c.id))
    for obligation_id, kind in obligation_specs:
        for probe in synthesize_acceptance_probes(model, obligation_id, kind):
            canonical = lowering.lower_population(model, probe.population, "postgres")
            mapped = _rewrite_program(model, canonical, mapping)
            rows.append(ExternalAcceptanceCase(
                obligation_id=obligation_id,
                kind=kind,
                probe_id=probe.probe_id,
                label=probe.label,
                source_probe=probe,
                program=mapped,
            ))
    return rows


def _acceptance_plan_sha256(model: Model, rows: list[ExternalAcceptanceCase]) -> str | None:
    if not rows:
        return None
    payload = [row.to_dict(model) for row in rows]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _refine_observation(negative: str | None, acceptance_rows: list[dict[str, Any]]) -> str | None:
    """Refine a negative-witness observation with source-valid acceptance probes.

    The result is deliberately probe-scoped.  Even when every generated probe
    is accepted, Factgraph says `preserved_on_tested_cases`, not exact
    equivalence of the whole source and target semantics.
    """
    if negative == "weakened":
        return "weakened"
    if negative != "preserved_or_stronger":
        return None
    observed = [r for r in acceptance_rows if r.get("status") == "observed"]
    if any(r.get("accepted") is False for r in observed):
        return "stronger_or_incompatible"
    if acceptance_rows and len(observed) == len(acceptance_rows) and all(r.get("accepted") is True for r in observed):
        return "preserved_on_tested_cases"
    return "preserved_or_stronger"


def _mapping_sha256(mapping: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _base_report(
    model: Model,
    artifact_sql: str,
    mapping: dict[str, Any],
    *,
    cases: list[SharedWitnessCase] | None = None,
    acceptance_cases: list[ExternalAcceptanceCase] | None = None,
) -> dict[str, Any]:
    rows = cases or []
    acceptance_rows = acceptance_cases or []
    counts: dict[str, int] = {}
    for case in rows:
        counts[case.program.status] = counts.get(case.program.status, 0) + 1
    acceptance_counts: dict[str, int] = {}
    for case in acceptance_rows:
        acceptance_counts[case.program.status] = acceptance_counts.get(case.program.status, 0) + 1
    return {
        "format": "factgraph-external-postgres-artifact-audit-v1",
        "target": "postgres_external",
        "model": model.name,
        "model_id": model.id,
        "model_semantic_sha256": model_semantic_sha256(model),
        "artifact_sha256": sha256_text(artifact_sql),
        "mapping_sha256": _mapping_sha256(mapping),
        "case_plan_sha256": case_plan_sha256(rows) if rows else None,
        "case_count": len(rows),
        "lowering_counts": counts,
        "acceptance_probe_plan_sha256": _acceptance_plan_sha256(model, acceptance_rows),
        "acceptance_probe_count": len(acceptance_rows),
        "acceptance_lowering_counts": acceptance_counts,
        "results": [],
        "acceptance_results": [],
        "cases": [case.to_dict() for case in rows],
        "acceptance_cases": [case.to_dict(model) for case in acceptance_rows],
        "rule": (
            "No preservation claim is inferred from the external artifact until source-semantic witnesses are observed against the supplied DDL. "
            "An invalid witness that survives is weakening evidence. Rejection alone means preservation-or-stronger; source-valid acceptance probes may refine that to "
            "preserved_on_tested_cases or stronger_or_incompatible. Even the refined result is probe-scoped, not a proof of exact equivalence."
        ),
    }


def invalid_mapping_report(model: Model, artifact_sql: str, mapping: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    return {
        **_base_report(model, artifact_sql, mapping),
        "status": "invalid_mapping",
        "mapping_validation": validation,
        "reason": "the explicit semantic-to-physical mapping did not validate; no witness execution was attempted",
    }


def generated_report(model: Model, artifact_sql: str, mapping: dict[str, Any]) -> dict[str, Any]:
    cases = build_external_cases(model, mapping)
    acceptance = build_external_acceptance_cases(model, mapping)
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.program.status] = counts.get(case.program.status, 0) + 1
    return {**_base_report(model, artifact_sql, mapping, cases=cases, acceptance_cases=acceptance), "status": "generated_not_run"}


def _required_physical_columns(model: Model, mapping: dict[str, Any]) -> dict[str, set[str]]:
    table_map, column_maps = _physical_maps(model, mapping)
    required: dict[str, set[str]] = {}
    for canonical, cmap in column_maps.items():
        required.setdefault(table_map[canonical], set()).update(cmap.values())
    return required


def _verify_live_structure(conn: Any, schema: str, model: Model, mapping: dict[str, Any]) -> dict[str, Any]:
    required = _required_physical_columns(model, mapping)
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    with conn.cursor() as cur:
        for table, cols in sorted(required.items()):
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            found = {row[0] for row in cur.fetchall()}
            if not found:
                missing_tables.append(table)
                continue
            missing = sorted(cols - found)
            if missing:
                missing_columns[table] = missing
    return {
        "passed": not missing_tables and not missing_columns,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "required_tables": sorted(required),
    }


def run_live(model: Model, artifact_sql: str, mapping: dict[str, Any], dsn: str) -> dict[str, Any]:
    validation = validate_mapping(model, artifact_sql, mapping)
    if not validation["passed"]:
        return invalid_mapping_report(model, artifact_sql, mapping, validation)
    cases = build_external_cases(model, mapping)
    acceptance_cases = build_external_acceptance_cases(model, mapping)
    base = generated_report(model, artifact_sql, mapping)
    try:
        import psycopg  # type: ignore
    except ImportError:
        return {**base, "status": "unavailable", "reason": "psycopg is not installed"}
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception as exc:
        return {**base, "status": "unavailable", "reason": f"connection failed: {type(exc).__name__}: {exc}"}

    preflight_schema = "fg_ext_preflight_" + hashlib.sha256((model.id + sha256_text(artifact_sql)).encode()).hexdigest()[:12]
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
        try:
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{preflight_schema}" CASCADE')
                cur.execute(f'CREATE SCHEMA "{preflight_schema}"')
                cur.execute(f'SET search_path TO "{preflight_schema}"')
                for stmt in _split_sql(artifact_sql):
                    cur.execute(stmt)
            structure = _verify_live_structure(conn, preflight_schema, model, mapping)
        except Exception as exc:
            return {**base, "status": "artifact_setup_failed", "server": version, "reason": f"external DDL preflight failed: {type(exc).__name__}: {exc}"}
        finally:
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{preflight_schema}" CASCADE')
            except Exception:
                pass
        if not structure["passed"]:
            return {**base, "status": "artifact_structure_mismatch", "server": version, "structure_verification": structure}

        results: list[dict[str, Any]] = []
        for case in cases:
            if case.program.status != "lowered":
                results.append(_evaluate(case, None)); continue
            schema = "fg_ext_" + hashlib.sha256(f"{model.id}\0{case.obligation_id}\0{base['artifact_sha256']}".encode()).hexdigest()[:12]
            setup_error = None
            actual = None
            err = None
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    cur.execute(f'CREATE SCHEMA "{schema}"')
                    cur.execute(f'SET search_path TO "{schema}"')
                    for stmt in _split_sql(artifact_sql):
                        cur.execute(stmt)
            except Exception as exc:
                setup_error = f"{type(exc).__name__}: {exc}"
            if setup_error is None:
                actual = "realized"
                try:
                    with conn.cursor() as cur:
                        for op in case.program.operations:
                            cur.execute(op["sql"].rstrip(";"))
                except Exception as exc:
                    actual = "prevented"; err = f"{type(exc).__name__}: {exc}"

            if setup_error is not None:
                result = _evaluate(case, None, error=f"artifact setup failed for case: {setup_error}")
                result["status"] = "setup_failed"
                result["semantic_observation"] = "not_observed"
                result["observed_preservation"] = None
                results.append(result)
            else:
                poststate_passed: bool | None = None
                poststate_results: list[dict[str, Any]] = []
                if actual == "realized" and case.poststate_required_for_semantic_proof:
                    if not case.postconditions:
                        poststate_passed = None
                    else:
                        poststate_passed = True
                        for condition in case.postconditions:
                            try:
                                with conn.cursor() as cur:
                                    cur.execute(condition["sql"])
                                    row = cur.fetchone()
                                actual_scalar = row[0] if row is not None else None
                                expected_scalar = condition.get("expect_scalar")
                                ok = actual_scalar == expected_scalar
                                poststate_results.append({
                                    "query": condition["sql"], "expected_scalar": expected_scalar,
                                    "actual_scalar": actual_scalar, "passed": ok,
                                })
                                poststate_passed = bool(poststate_passed and ok)
                            except Exception as exc:
                                poststate_passed = None
                                poststate_results.append({"query": condition.get("sql"), "passed": None, "error": f"{type(exc).__name__}: {exc}"})
                results.append(_evaluate(case, actual, error=err, poststate_passed=poststate_passed, poststate_results=poststate_results))
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            except Exception:
                pass

        acceptance_results: list[dict[str, Any]] = []
        for probe in acceptance_cases:
            if probe.program.status != "lowered":
                acceptance_results.append({
                    "probe_id": probe.probe_id,
                    "obligation_id": probe.obligation_id,
                    "kind": probe.kind,
                    "label": probe.label,
                    "status": "not_executable",
                    "accepted": None,
                    "error": "; ".join(probe.program.limitations) if probe.program.limitations else None,
                })
                continue
            schema = "fg_ext_accept_" + hashlib.sha256(
                f"{model.id}\0{probe.probe_id}\0{base['artifact_sha256']}".encode()
            ).hexdigest()[:12]
            setup_error = None
            accepted: bool | None = None
            err = None
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    cur.execute(f'CREATE SCHEMA "{schema}"')
                    cur.execute(f'SET search_path TO "{schema}"')
                    for stmt in _split_sql(artifact_sql):
                        cur.execute(stmt)
            except Exception as exc:
                setup_error = f"{type(exc).__name__}: {exc}"
            if setup_error is None:
                accepted = True
                try:
                    with conn.cursor() as cur:
                        for op in probe.program.operations:
                            cur.execute(op["sql"].rstrip(";"))
                except Exception as exc:
                    accepted = False
                    err = f"{type(exc).__name__}: {exc}"
            acceptance_results.append({
                "probe_id": probe.probe_id,
                "obligation_id": probe.obligation_id,
                "kind": probe.kind,
                "label": probe.label,
                "status": "setup_failed" if setup_error is not None else "observed",
                "accepted": accepted,
                "error": f"artifact setup failed for probe: {setup_error}" if setup_error is not None else err,
            })
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            except Exception:
                pass

        acceptance_by_obligation: dict[str, list[dict[str, Any]]] = {}
        for row in acceptance_results:
            acceptance_by_obligation.setdefault(str(row.get("obligation_id")), []).append(row)
        for row in results:
            oid = str(row.get("obligation_id"))
            probes = acceptance_by_obligation.get(oid, [])
            row["acceptance_probe_results"] = probes
            row["refined_observation"] = _refine_observation(row.get("observed_preservation"), probes)

        counts = {"preserved_or_stronger": 0, "weakened": 0, "unresolved": 0, "not_observed": 0}
        for row in results:
            obs = row.get("observed_preservation")
            if obs in {"preserved_or_stronger", "weakened"}:
                counts[obs] += 1
            elif row.get("status") == "not_executable":
                counts["not_observed"] += 1
            else:
                counts["unresolved"] += 1
        refined_counts: dict[str, int] = {}
        for row in results:
            refined = row.get("refined_observation") or "unresolved"
            refined_counts[refined] = refined_counts.get(refined, 0) + 1
        acceptance_counts = {
            "accepted": sum(r.get("status") == "observed" and r.get("accepted") is True for r in acceptance_results),
            "rejected": sum(r.get("status") == "observed" and r.get("accepted") is False for r in acceptance_results),
            "unresolved": sum(r.get("status") != "observed" for r in acceptance_results),
        }
        return {
            **base,
            "status": "completed",
            "server": version,
            "structure_verification": structure,
            "results": results,
            "acceptance_results": acceptance_results,
            "observed_counts": counts,
            "refined_observed_counts": refined_counts,
            "acceptance_observed_counts": acceptance_counts,
            "observed_case_count": counts["preserved_or_stronger"] + counts["weakened"],
            "unresolved_case_count": counts["unresolved"],
        }
    finally:
        conn.close()


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# External PostgreSQL semantic audit — {report.get('model')}", "",
        f"- Status: **{report.get('status')}**",
        f"- Source semantic SHA-256: `{report.get('model_semantic_sha256')}`",
        f"- PostgreSQL artifact SHA-256: `{report.get('artifact_sha256')}`",
        f"- Mapping SHA-256: `{report.get('mapping_sha256')}`",
        "",
        "> This audit does not assume the external schema has Factgraph's canonical constraints. It executes source-semantic invalid populations against the explicitly mapped physical layout. `preserved_or_stronger` means the invalid witness did not survive; source-valid acceptance probes can refine that to `preserved_on_tested_cases` or `stronger_or_incompatible`. None of these probe-scoped results is a proof of exact semantic equivalence.",
        "",
    ]
    if report.get("status") == "completed":
        counts = report.get("observed_counts", {})
        lines += [
            "## Observed result", "",
            f"- Invalid witnesses prevented / otherwise not realized: **{counts.get('preserved_or_stronger', 0)}**",
            f"- Invalid source states realized: **{counts.get('weakened', 0)}**",
            f"- Unresolved live cases: **{counts.get('unresolved', 0)}**",
            f"- Non-executable lowering gaps: **{counts.get('not_observed', 0)}**",
            f"- Source-valid acceptance probes accepted: **{report.get('acceptance_observed_counts', {}).get('accepted', 0)}**",
            f"- Source-valid acceptance probes rejected: **{report.get('acceptance_observed_counts', {}).get('rejected', 0)}**",
            "",
        ]
    lines += ["## Obligation cases", "", "| Obligation | Kind | Lowering | Negative witness | Refined observation |", "| --- | --- | --- | --- | --- |"]
    observed = {r.get("obligation_id"): r for r in report.get("results", [])}
    for case in report.get("cases", []):
        row = observed.get(case["obligation_id"], {})
        lines.append(f"| `{case['obligation_id']}` | {case['kind']} | {case['program']['status']} | {row.get('observed_preservation') or 'not observed'} | {row.get('refined_observation') or 'not observed'} |")
    if report.get("acceptance_cases"):
        lines += ["", "## Source-valid acceptance probes", "", "| Obligation | Probe | Lowering | Live result |", "| --- | --- | --- | --- |"]
        acceptance_observed = {r.get("probe_id"): r for r in report.get("acceptance_results", [])}
        for case in report.get("acceptance_cases", []):
            row = acceptance_observed.get(case["probe_id"], {})
            live = "accepted" if row.get("accepted") is True else "rejected" if row.get("accepted") is False else "not observed"
            lines.append(f"| `{case['obligation_id']}` | {case['label']} | {case['program']['status']} | {live} |")
    return "\n".join(lines) + "\n"


def write_bundle(model: Model, artifact_sql: str, mapping: dict[str, Any], out_dir: Path, *, dsn: str | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    validation = validate_mapping(model, artifact_sql, mapping)
    (out_dir / "artifact.sql").write_text(artifact_sql, encoding="utf-8")
    (out_dir / "mapping.json").write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "mapping_validation.json").write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not validation["passed"]:
        report = invalid_mapping_report(model, artifact_sql, mapping, validation)
    elif dsn:
        report = run_live(model, artifact_sql, mapping, dsn)
    else:
        report = generated_report(model, artifact_sql, mapping)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    cases = build_external_cases(model, mapping) if validation["passed"] else []
    acceptance_cases = build_external_acceptance_cases(model, mapping) if validation["passed"] else []
    witness_dir = out_dir / "witnesses"
    witness_dir.mkdir(exist_ok=True)
    index = []
    for case in cases:
        safe = artifact_filename_token(case.obligation_id)
        path = witness_dir / f"{safe}.sql"
        path.write_text("\n".join(op["sql"] for op in case.program.operations) + ("\n" if case.program.operations else ""), encoding="utf-8")
        index.append({"obligation_id": case.obligation_id, "lowering_status": case.program.status, "file": f"witnesses/{path.name}"})
    (witness_dir / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    acceptance_dir = out_dir / "acceptance_probes"
    acceptance_dir.mkdir(exist_ok=True)
    acceptance_index = []
    for case in acceptance_cases:
        safe = artifact_filename_token(case.probe_id)
        path = acceptance_dir / f"{safe}.sql"
        path.write_text("\n".join(op["sql"] for op in case.program.operations) + ("\n" if case.program.operations else ""), encoding="utf-8")
        acceptance_index.append({
            "obligation_id": case.obligation_id,
            "kind": case.kind,
            "probe_id": case.probe_id,
            "label": case.label,
            "lowering_status": case.program.status,
            "file": f"acceptance_probes/{path.name}",
        })
    (acceptance_dir / "index.json").write_text(json.dumps(acceptance_index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
