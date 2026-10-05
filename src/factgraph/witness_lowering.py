from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, Iterable

from .ids import slug
from .model import EntityType, Model, ObjectifiedFactType, ValueType
from .population import SemanticPopulation
from .targets import mongo, postgres, typedb


@dataclass(frozen=True)
class LoweringProgram:
    """A deterministic attempt to realize one semantic population in one target.

    `status=lowered` means every source population atom in the supplied population
    has a target-native representation in the emitted operation sequence. It does
    *not* mean the operations will be accepted: rejection is precisely the desired
    observation when the target preserves the violated source obligation.

    Other statuses are deliberately evidence, not errors. They prevent the audit
    layer from claiming that a different or hand-authored backend witness is the
    same conceptual population.
    """

    target: str
    status: str  # lowered | representation_prevents_exact_realization | unsupported
    fidelity: str
    operations: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "factgraph-shared-witness-lowering-v1",
            **asdict(self),
            "operations": list(self.operations),
            "limitations": list(self.limitations),
            "notes": list(self.notes),
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$fg_type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$fg_type": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$fg_type": "decimal", "value": str(value)}
    return value


def _stable_object_id(instance_id: str) -> str:
    # Deterministic ObjectId-shaped 12-byte / 24-hex token. It is a target support
    # identity only; it is never promoted to a conceptual Factgraph identifier.
    return hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:24]


def _hints_for_owner(model: Model, owner_id: str):
    return sorted(
        (h for h in model.field_hints.values() if h.owner_object_type_id == owner_id),
        key=lambda h: (not h.identifier_component, h.field_name, h.field_fact_id),
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


def _field_positions(model: Model, field_fact_id: str) -> tuple[int, int]:
    hint = model.field_hints[field_fact_id]
    fact = model.fact_types[field_fact_id]
    owner_positions = [r.ordinal for r in fact.roles if r.player_id == hint.owner_object_type_id]
    value_positions = [r.ordinal for r in fact.roles if r.player_id == hint.value_type_id]
    if len(owner_positions) != 1 or len(value_positions) != 1:
        raise ValueError(f"field fact {field_fact_id} does not have an unambiguous owner/value incidence")
    return owner_positions[0], value_positions[0]


def _field_value_instances(model: Model, pop: SemanticPopulation, field_fact_id: str, owner_instance: str) -> list[str]:
    owner_pos, value_pos = _field_positions(model, field_fact_id)
    return [row[value_pos] for row in pop.facts.get(field_fact_id, []) if len(row) > max(owner_pos, value_pos) and row[owner_pos] == owner_instance]


def _field_literals(model: Model, pop: SemanticPopulation, field_fact_id: str, owner_instance: str) -> list[Any]:
    out: list[Any] = []
    for iid in _field_value_instances(model, pop, field_fact_id, owner_instance):
        if iid not in pop.values:
            raise ValueError(f"field fact {field_fact_id} references value instance without literal: {iid}")
        out.append(pop.values[iid])
    return out


def _stable_bigint(instance_id: str) -> int:
    # Positive deterministic 60-bit support identity, safe in PostgreSQL BIGINT.
    return int(hashlib.sha256(instance_id.encode("utf-8")).hexdigest()[:15], 16) + 1


def _support_literal(model: Model, value_type: ValueType, seed: str) -> Any:
    # Prefer a declared valid domain member because transport identity must not
    # accidentally introduce a second, unrelated value-domain violation.
    for c in model.constraints_for_value(value_type.id):
        spec = c.value_spec or {}
        if spec.get("kind") == "oneof" and spec.get("values"):
            return list(spec["values"])[0]
    for c in model.constraints_for_value(value_type.id):
        spec = c.value_spec or {}
        if spec.get("kind") == "range":
            return spec.get("min")
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    if value_type.scalar_kind == "Int":
        return int(digest[:8], 16) % 1_000_000 + 1
    if value_type.scalar_kind in {"Float", "Decimal"}:
        return int(digest[:8], 16) % 1_000_000 + 0.5
    if value_type.scalar_kind == "Bool":
        return False
    if value_type.scalar_kind == "Date":
        return "2026-01-01"
    if value_type.scalar_kind == "Timestamp":
        return "2026-01-01T00:00:00+00:00"
    if value_type.scalar_kind == "UUID":
        h = digest[:32]
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
    return f"__fg_support_{digest[:16]}"


def _invalid_subtype_instance(model: Model, pop: SemanticPopulation, entity: EntityType, iid: str) -> bool:
    c = model.subtype_constraint(entity.id)
    return bool(c is not None and c.supertype_id and iid not in pop.memberships.get(c.supertype_id, set()))


def subtype_support_key_values(model: Model, entity: EntityType, iid: str) -> list[tuple[Any, Any]]:
    """Return (field hint, valid transport literal) pairs for an invalid subtype row."""
    out: list[tuple[Any, Any]] = []
    for hint in _identifier_hints(model, entity.id):
        vt = model.object_types[hint.value_type_id]
        assert isinstance(vt, ValueType)
        out.append((hint, _support_literal(model, vt, f"{entity.id}|{iid}|{hint.field_fact_id}")))
    return out


def _binding_for_instance(pop: SemanticPopulation, instance_id: str) -> tuple[str, str, int] | None:
    return pop.objectifications.get(instance_id)


def _binding_for_occurrence(pop: SemanticPopulation, fact_id: str, row_index: int) -> tuple[str, str] | None:
    for iid, (oid, fid, idx) in sorted(pop.objectifications.items()):
        if fid == fact_id and idx == row_index:
            return oid, iid
    return None


def _unresolved_objectification_dependencies(model: Model, pop: SemanticPopulation) -> list[str]:
    reasons: list[str] = []
    for obj in sorted((o for o in model.object_types.values() if isinstance(o, ObjectifiedFactType)), key=lambda o: o.id):
        # If object-owned fields exist, every materialized occurrence needs a
        # binding so those fields (including intentionally missing ones) are
        # projected onto the correct physical relation row. Without it, a DB
        # rejection could be caused by omitted context rather than the intended
        # source obligation.
        if any(h.owner_object_type_id == obj.id for h in model.field_hints.values()):
            for row_index, _row in enumerate(pop.facts.get(obj.fact_type_id, [])):
                if _binding_for_occurrence(pop, obj.fact_type_id, row_index) is None:
                    reasons.append(
                        f"objectified fact occurrence {obj.fact_type_id}[{row_index}] has no objectification occurrence-identity binding required to project fields of {obj.name}"
                    )
        used_instances: set[str] = set(pop.memberships.get(obj.id, set()))
        for fact in model.fact_types.values():
            for role in fact.roles:
                if role.player_id != obj.id:
                    continue
                for row in pop.facts.get(fact.id, []):
                    if len(row) > role.ordinal:
                        used_instances.add(row[role.ordinal])
        for hint in model.field_hints.values():
            if hint.owner_object_type_id != obj.id:
                continue
            fact = model.fact_types[hint.field_fact_id]
            owner_role = next(r for r in fact.roles if r.player_id == obj.id)
            for row in pop.facts.get(fact.id, []):
                if len(row) > owner_role.ordinal:
                    used_instances.add(row[owner_role.ordinal])
        for iid in sorted(used_instances):
            binding = _binding_for_instance(pop, iid)
            if binding is None:
                reasons.append(f"objectified instance {iid} of {obj.name} has no occurrence binding")
                continue
            oid, fid, row_index = binding
            if oid != obj.id or fid != obj.fact_type_id or row_index < 0 or row_index >= len(pop.facts.get(fid, [])):
                reasons.append(f"objectified instance {iid} has an invalid occurrence binding")
    return reasons



def _invalid_subtype_memberships(model: Model, pop: SemanticPopulation) -> list[str]:
    out: list[str] = []
    for c in sorted(model.constraints.values(), key=lambda c: c.id):
        if c.kind.value != "subtype" or not c.subtype_id or not c.supertype_id:
            continue
        sub = pop.memberships.get(c.subtype_id, set())
        sup = pop.memberships.get(c.supertype_id, set())
        for iid in sorted(sub - sup):
            out.append(
                f"subtype instance {iid} is intentionally absent from supertype population; target-native subtype testing requires an encoding identity/support value plus a post-state entailment check"
            )
    return out


def _standalone_values(pop: SemanticPopulation) -> list[str]:
    used = {iid for rows in pop.facts.values() for row in rows for iid in row}
    return sorted(iid for iid in pop.values if iid not in used)


def _entity_key_values(model: Model, pop: SemanticPopulation, entity: EntityType, instance_id: str) -> tuple[list[tuple[str, Any]], list[str]]:
    hints = _identifier_hints(model, entity.id)
    if not hints:
        return [], [f"entity {entity.name} uses a generated target identity; source instance {instance_id} has no portable key"]
    pairs: list[tuple[str, Any]] = []
    problems: list[str] = []
    for hint in hints:
        vals = _field_literals(model, pop, hint.field_fact_id, instance_id)
        if len(vals) != 1:
            problems.append(f"source instance {instance_id} has {len(vals)} values for identifier field {hint.field_name}; a reference needs exactly one")
        else:
            pairs.append((hint.field_name, vals[0]))
    return pairs, problems


def _entity_multivalued_fields(model: Model, pop: SemanticPopulation) -> list[str]:
    problems: list[str] = []
    for obj in sorted((o for o in model.object_types.values() if isinstance(o, (EntityType, ObjectifiedFactType))), key=lambda o: o.id):
        for iid in sorted(pop.memberships.get(obj.id, set())):
            for hint in _hints_for_owner(model, obj.id):
                vals = _field_literals(model, pop, hint.field_fact_id, iid)
                if len(vals) > 1:
                    problems.append(f"{obj.name} instance {iid} has {len(vals)} values for scalar field {hint.field_name}")
    return problems


def _postgres_insert(table: str, values: list[tuple[str, Any, str]], *, override_identity: bool = False) -> str:
    if not values:
        return f"INSERT INTO {table} DEFAULT VALUES;"
    cols = ", ".join(col for col, _value, _kind in values)
    vals = ", ".join(postgres._sql_literal(value, kind) for _col, value, kind in values)
    override = " OVERRIDING SYSTEM VALUE" if override_identity else ""
    return f"INSERT INTO {table} ({cols}){override} VALUES ({vals});"


def _postgres_entity_ops(model: Model, pop: SemanticPopulation) -> tuple[list[dict[str, Any]], list[str]]:
    ops: list[dict[str, Any]] = []
    problems: list[str] = []
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        direct_hints = _hints_for_owner(model, entity.id)
        subtype = model.subtype_constraint(entity.id)
        effective_ids = _identifier_hints(model, entity.id)
        for iid in sorted(pop.memberships.get(entity.id, set())):
            values: list[tuple[str, Any, str]] = []
            seen_cols: set[str] = set()
            # Subtype tables materialize inherited keys even though the source field
            # fact belongs to the supertype.
            for hint in effective_ids if subtype is not None else direct_hints:
                if subtype is None and not hint.identifier_component:
                    continue
                literals = _field_literals(model, pop, hint.field_fact_id, iid)
                if len(literals) > 1:
                    problems.append(f"cannot place multiple {hint.field_name} values into PostgreSQL scalar column for {iid}")
                    continue
                if len(literals) == 1:
                    vt = model.object_types[hint.value_type_id]
                    assert isinstance(vt, ValueType)
                    col = postgres.pg_name(hint.field_name)
                    values.append((col, literals[0], vt.scalar_kind)); seen_cols.add(col)
                elif len(literals) == 0 and subtype is not None and _invalid_subtype_instance(model, pop, entity, iid):
                    vt = model.object_types[hint.value_type_id]
                    assert isinstance(vt, ValueType)
                    col = postgres.pg_name(hint.field_name)
                    values.append((col, _support_literal(model, vt, f"{entity.id}|{iid}|{hint.field_fact_id}"), vt.scalar_kind)); seen_cols.add(col)
            for hint in direct_hints:
                if hint.identifier_component:
                    continue
                literals = _field_literals(model, pop, hint.field_fact_id, iid)
                if len(literals) > 1:
                    problems.append(f"cannot place multiple {hint.field_name} values into PostgreSQL scalar column for {iid}")
                    continue
                if len(literals) == 1:
                    vt = model.object_types[hint.value_type_id]
                    assert isinstance(vt, ValueType)
                    col = postgres.pg_name(hint.field_name)
                    if col not in seen_cols:
                        values.append((col, literals[0], vt.scalar_kind)); seen_cols.add(col)
            # An entity without a source identifier uses generated identity in the
            # target. A standalone instance can be inserted, but references cannot
            # be resolved later; the fact lowering phase diagnoses those.
            ops.append({"op": "sql", "source_atom": {"membership": [entity.id, iid]}, "sql": _postgres_insert(postgres.pg_name(entity.name), values)})
    return ops, problems


def _postgres_role_values(model: Model, pop: SemanticPopulation, role, iid: str) -> tuple[list[tuple[str, Any, str]], list[str]]:
    player = model.object_types[role.player_id]
    cols = postgres._role_columns(model, role)
    if isinstance(player, ValueType):
        if iid not in pop.values:
            return [], [f"role {role.name} references value instance {iid} with no literal"]
        return [(cols[0][0], pop.values[iid], player.scalar_kind)], []
    if isinstance(player, EntityType):
        pairs, problems = _entity_key_values(model, pop, player, iid)
        if problems:
            return [], problems
        hints = _identifier_hints(model, player.id)
        return [(col_name, value, model.object_types[hint.value_type_id].scalar_kind) for (col_name, _sql_type), (_field, value), hint in zip(cols, pairs, hints)], []
    if isinstance(player, ObjectifiedFactType):
        binding = _binding_for_instance(pop, iid)
        if binding is None:
            return [], [f"objectified role {role.name} references {iid} without occurrence identity"]
        return [(cols[0][0], _stable_bigint(iid), "Int")], []
    return [], [f"unsupported role player for {role.name}"]



def _identifier_reference_gap_only(problems: list[str]) -> bool:
    return bool(problems) and all("identifier field" in p and "has 0 values" in p for p in problems)

def lower_postgres(model: Model, pop: SemanticPopulation) -> LoweringProgram:
    subtype_gap = _invalid_subtype_memberships(model, pop)
    standalone = _standalone_values(pop)
    if standalone:
        return LoweringProgram("postgres", "unsupported", "source_population_requires_contextual_embedding", limitations=tuple(f"standalone source value instance {iid} has no physical PostgreSQL row in the current projection" for iid in standalone))
    objectification = _unresolved_objectification_dependencies(model, pop)
    if objectification:
        return LoweringProgram("postgres", "unsupported", "source_population_not_fully_addressable", limitations=tuple(objectification))
    multivalued = _entity_multivalued_fields(model, pop)
    if multivalued:
        return LoweringProgram(
            "postgres", "representation_prevents_exact_realization", "scalar_field_projection_collapses_source_facts",
            limitations=tuple(multivalued),
            notes=("PostgreSQL entity fields are compiled as one scalar column per owner; two source field facts for one owner cannot coexist as two physical values.",),
        )
    ops, problems = _postgres_entity_ops(model, pop)
    source_facts = sorted(
        (f for f in model.fact_types.values() if f.id not in model.field_hints),
        key=lambda f: (any(isinstance(model.object_types[r.player_id], ObjectifiedFactType) for r in f.roles), f.id),
    )
    for fact in source_facts:
        obj = model.objectification_for_fact(fact.id)
        for row_index, row in enumerate(pop.facts.get(fact.id, [])):
            values: list[tuple[str, Any, str]] = []
            row_problems: list[str] = []
            bound = _binding_for_occurrence(pop, fact.id, row_index) if obj is not None else None
            if obj is not None:
                support_iid = bound[1] if bound is not None else f"execution:occurrence:{fact.id}:{row_index}"
                values.append(("id", _stable_bigint(support_iid), "Int"))
            for role, iid in zip(sorted(fact.roles, key=lambda r: r.ordinal), row):
                rv, rp = _postgres_role_values(model, pop, role, iid)
                values.extend(rv); row_problems.extend(rp)
            if obj is not None and bound is not None:
                owner_iid = bound[1]
                for hint in _hints_for_owner(model, obj.id):
                    literals = _field_literals(model, pop, hint.field_fact_id, owner_iid)
                    if len(literals) > 1:
                        row_problems.append(f"cannot place multiple {hint.field_name} values into PostgreSQL scalar column for {owner_iid}")
                    elif len(literals) == 1:
                        vt = model.object_types[hint.value_type_id]
                        assert isinstance(vt, ValueType)
                        values.append((postgres.pg_name(hint.field_name), literals[0], vt.scalar_kind))
            problems.extend(row_problems)
            if not row_problems:
                ops.append({
                    "op": "sql",
                    "source_atom": {"fact_type_id": fact.id, "row_index": row_index, "row": list(row)},
                    "objectified_instance_id": bound[1] if bound is not None else None,
                    "sql": _postgres_insert(postgres.pg_name(fact.name), values, override_identity=obj is not None),
                })
    if problems:
        if _identifier_reference_gap_only(problems):
            return LoweringProgram(
                "postgres", "representation_prevents_exact_realization",
                "identifier_projection_requires_missing_source_value", tuple(ops), tuple(sorted(set(problems))),
                notes=("The source witness intentionally contains an entity with no identifier value while another fact refers to that entity. PostgreSQL's canonical FK representation uses the semantic identifier itself, so the isolated source state cannot be represented without inventing the missing identifier.",),
            )
        return LoweringProgram("postgres", "unsupported", "source_population_requires_unrepresented_target_identity", tuple(ops), tuple(sorted(set(problems))))
    if subtype_gap:
        return LoweringProgram(
            "postgres", "lowered", "source_core_with_target_support_identity", tuple(ops),
            notes=(
                "The invalid subtype source state has no inherited identifier fact because it intentionally lacks supertype membership. A deterministic valid transport key is supplied only to address the subtype table row; no supertype row is created. PostgreSQL's FK is then the semantic observation.",
            ),
        )
    return LoweringProgram("postgres", "lowered", "exact_source_atom_projection", tuple(ops), notes=("Operations preserve source multiplicity and are intentionally not pre-deduplicated; target rejection/acceptance is the observation.",))


def _mongo_entity_document(model: Model, pop: SemanticPopulation, entity: EntityType, iid: str) -> tuple[dict[str, Any], list[str]]:
    doc: dict[str, Any] = {}
    problems: list[str] = []
    id_hints = _identifier_hints(model, entity.id)
    if not id_hints:
        doc["_id"] = {"$fg_type": "objectId", "value": _stable_object_id(iid)}
    else:
        for hint in id_hints:
            vals = _field_literals(model, pop, hint.field_fact_id, iid)
            if len(vals) > 1:
                problems.append(f"cannot place multiple {hint.field_name} values into MongoDB scalar property for {iid}")
            elif len(vals) == 1:
                doc[slug(hint.field_name)] = _json_value(vals[0])
            elif _invalid_subtype_instance(model, pop, entity, iid):
                vt = model.object_types[hint.value_type_id]
                assert isinstance(vt, ValueType)
                doc[slug(hint.field_name)] = _json_value(_support_literal(model, vt, f"{entity.id}|{iid}|{hint.field_fact_id}"))
    for hint in _hints_for_owner(model, entity.id):
        if hint.identifier_component and hint in id_hints:
            continue
        vals = _field_literals(model, pop, hint.field_fact_id, iid)
        if len(vals) > 1:
            problems.append(f"cannot place multiple {hint.field_name} values into MongoDB scalar property for {iid}")
        elif len(vals) == 1:
            doc[slug(hint.field_name)] = _json_value(vals[0])
    return doc, problems


def _mongo_role_fields(model: Model, pop: SemanticPopulation, role, iid: str) -> tuple[dict[str, Any], list[str]]:
    player = model.object_types[role.player_id]
    names = list(mongo._role_properties(model, role))
    if isinstance(player, ValueType):
        if iid not in pop.values:
            return {}, [f"role {role.name} references value instance {iid} with no literal"]
        return {names[0]: _json_value(pop.values[iid])}, []
    if isinstance(player, EntityType):
        id_hints = _identifier_hints(model, player.id)
        if not id_hints:
            return {names[0]: {"$fg_type": "objectId", "value": _stable_object_id(iid)}}, []
        pairs, problems = _entity_key_values(model, pop, player, iid)
        if problems:
            return {}, problems
        return {name: _json_value(value) for name, (_field, value) in zip(names, pairs)}, []
    if isinstance(player, ObjectifiedFactType):
        binding = _binding_for_instance(pop, iid)
        if binding is None:
            return {}, [f"objectified role {role.name} references {iid} without occurrence identity"]
        return {names[0]: {"$fg_type": "objectId", "value": _stable_object_id(iid)}}, []
    return {}, [f"unsupported role player for {role.name}"]


def lower_mongo(model: Model, pop: SemanticPopulation) -> LoweringProgram:
    subtype_gap = _invalid_subtype_memberships(model, pop)
    standalone = _standalone_values(pop)
    if standalone:
        return LoweringProgram("mongo", "unsupported", "source_population_requires_contextual_embedding", limitations=tuple(f"standalone source value instance {iid} has no physical MongoDB document in the current projection" for iid in standalone))
    objectification = _unresolved_objectification_dependencies(model, pop)
    if objectification:
        return LoweringProgram("mongo", "unsupported", "source_population_not_fully_addressable", limitations=tuple(objectification))
    multivalued = _entity_multivalued_fields(model, pop)
    if multivalued:
        return LoweringProgram(
            "mongo", "representation_prevents_exact_realization", "scalar_property_projection_collapses_source_facts",
            limitations=tuple(multivalued),
            notes=("MongoDB entity fields are projected as one scalar property; duplicate JSON keys are not a faithful representation of two conceptual field facts.",),
        )
    ops: list[dict[str, Any]] = []
    problems: list[str] = []
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        for iid in sorted(pop.memberships.get(entity.id, set())):
            doc, p = _mongo_entity_document(model, pop, entity, iid)
            problems.extend(p)
            ops.append({"op": "insert_one", "source_atom": {"membership": [entity.id, iid]}, "collection": slug(entity.name), "document": doc})
    source_facts = sorted(
        (f for f in model.fact_types.values() if f.id not in model.field_hints),
        key=lambda f: (any(isinstance(model.object_types[r.player_id], ObjectifiedFactType) for r in f.roles), f.id),
    )
    for fact in source_facts:
        obj = model.objectification_for_fact(fact.id)
        for row_index, row in enumerate(pop.facts.get(fact.id, [])):
            doc: dict[str, Any] = {}
            row_problems: list[str] = []
            bound = _binding_for_occurrence(pop, fact.id, row_index) if obj is not None else None
            if obj is not None:
                support_iid = bound[1] if bound is not None else f"execution:occurrence:{fact.id}:{row_index}"
                doc["_id"] = {"$fg_type": "objectId", "value": _stable_object_id(support_iid)}
            for role, iid in zip(sorted(fact.roles, key=lambda r: r.ordinal), row):
                vals, p = _mongo_role_fields(model, pop, role, iid)
                overlap = set(doc).intersection(vals)
                if overlap:
                    p.append(f"role field collision in {fact.name}: {sorted(overlap)}")
                doc.update(vals); row_problems.extend(p)
            if obj is not None and bound is not None:
                owner_iid = bound[1]
                for hint in _hints_for_owner(model, obj.id):
                    literals = _field_literals(model, pop, hint.field_fact_id, owner_iid)
                    if len(literals) > 1:
                        row_problems.append(f"cannot place multiple {hint.field_name} values into MongoDB scalar property for {owner_iid}")
                    elif len(literals) == 1:
                        doc[slug(hint.field_name)] = _json_value(literals[0])
            problems.extend(row_problems)
            if not row_problems:
                ops.append({
                    "op": "insert_one",
                    "source_atom": {"fact_type_id": fact.id, "row_index": row_index, "row": list(row)},
                    "objectified_instance_id": bound[1] if bound is not None else None,
                    "collection": slug(fact.name), "document": doc,
                })
    if problems:
        if _identifier_reference_gap_only(problems):
            return LoweringProgram(
                "mongo", "representation_prevents_exact_realization",
                "identifier_projection_requires_missing_source_value", tuple(ops), tuple(sorted(set(problems))),
                notes=("The source witness intentionally contains an entity with no identifier value while another fact refers to that entity. MongoDB's canonical relationship projection stores that semantic identifier in the reference field, so the isolated source state cannot be represented without inventing the missing identifier.",),
            )
        return LoweringProgram("mongo", "unsupported", "source_population_requires_unrepresented_target_identity", tuple(ops), tuple(sorted(set(problems))))
    if subtype_gap:
        return LoweringProgram(
            "mongo", "lowered", "source_core_with_target_support_identity", tuple(ops),
            notes=(
                "The invalid subtype source state has no inherited identifier fact because it intentionally lacks supertype membership. A deterministic valid transport key is added to the subtype document only so the physical collection can represent the object; no supertype document is created.",
            ),
        )
    return LoweringProgram("mongo", "lowered", "exact_source_atom_projection", tuple(ops), notes=("Operations preserve source fact-row multiplicity and use deterministic ObjectId support identities only when the target mapping itself uses synthetic identity.",))


def _typedb_var(prefix: str, text: str) -> str:
    return f"${prefix}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:10]}"


def _typedb_entity_clause(model: Model, pop: SemanticPopulation, entity: EntityType, iid: str) -> tuple[str, list[str]]:
    var = _typedb_var("e", entity.id + "|" + iid)
    chunks = [f"{var} isa {typedb.entity_label(entity)}"]
    problems: list[str] = []
    # TypeDB can faithfully represent multiple owned attributes even if a target
    # @card constraint later rejects them, so do not collapse field facts here.
    for hint in _hints_for_owner(model, entity.id):
        vt = model.object_types[hint.value_type_id]
        assert isinstance(vt, ValueType)
        for literal in _field_literals(model, pop, hint.field_fact_id, iid):
            chunks.append(f"has {typedb.field_attribute_label(model, hint.field_fact_id)} {typedb._typeql_literal(literal, vt.scalar_kind)}")
    return ", ".join(chunks), problems


def lower_typedb(model: Model, pop: SemanticPopulation) -> LoweringProgram:
    subtype_gap = _invalid_subtype_memberships(model, pop)
    if subtype_gap:
        return LoweringProgram(
            "typedb", "representation_prevents_exact_realization",
            "native_subtype_entailment_prevents_invalid_membership", limitations=tuple(subtype_gap),
            notes=("In TypeDB an instance of a subtype is semantically also an instance of its supertype. The source-invalid state 'Employee but not Person' therefore has no exact target population; this is a target-semantic preservation boundary, not a missing lowerer.",),
        )
    objectification = _unresolved_objectification_dependencies(model, pop)
    if objectification:
        return LoweringProgram("typedb", "unsupported", "source_population_not_fully_addressable", limitations=tuple(objectification))

    clauses: list[str] = []
    problems: list[str] = []
    entity_vars: dict[tuple[str, str], str] = {}
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        for iid in sorted(pop.memberships.get(entity.id, set())):
            clause, p = _typedb_entity_clause(model, pop, entity, iid)
            problems.extend(p)
            clauses.append(clause)
            entity_vars[(entity.id, iid)] = _typedb_var("e", entity.id + "|" + iid)

    objectified_vars: dict[tuple[str, str], str] = {}
    source_facts = sorted(
        (f for f in model.fact_types.values() if f.id not in model.field_hints),
        key=lambda f: (any(isinstance(model.object_types[r.player_id], ObjectifiedFactType) for r in f.roles), f.id),
    )
    for fact in source_facts:
        obj = model.objectification_for_fact(fact.id)
        for row_index, row in enumerate(pop.facts.get(fact.id, [])):
            bound = _binding_for_occurrence(pop, fact.id, row_index) if obj is not None else None
            rel_key = bound[1] if bound is not None else f"{fact.id}|{row_index}|{'|'.join(row)}"
            rel_var = _typedb_var("r", rel_key)
            if obj is not None and bound is not None:
                objectified_vars[(obj.id, bound[1])] = rel_var
            links: list[str] = []
            has: list[str] = []
            for role, iid in zip(sorted(fact.roles, key=lambda r: r.ordinal), row):
                player = model.object_types[role.player_id]
                if isinstance(player, ValueType):
                    if iid not in pop.values:
                        problems.append(f"role {role.name} references value instance {iid} with no literal")
                    else:
                        has.append(f"has {typedb.value_role_attribute_label(model, fact.id, role.id)} {typedb._typeql_literal(pop.values[iid], player.scalar_kind)}")
                elif isinstance(player, EntityType):
                    var = entity_vars.get((player.id, iid))
                    if var is None:
                        problems.append(f"relation {fact.name} references {player.name} instance {iid} without explicit membership")
                    else:
                        links.append(f"{typedb.role_label(role.name)}: {var}")
                elif isinstance(player, ObjectifiedFactType):
                    var = objectified_vars.get((player.id, iid))
                    if var is None:
                        problems.append(f"relation {fact.name} references objectified {player.name} instance {iid} without a lowered occurrence")
                    else:
                        links.append(f"{typedb.role_label(role.name)}: {var}")
                else:
                    problems.append(f"unsupported role player {role.name}")
            if obj is not None and bound is not None:
                owner_iid = bound[1]
                for hint in _hints_for_owner(model, obj.id):
                    vt = model.object_types[hint.value_type_id]
                    assert isinstance(vt, ValueType)
                    for literal in _field_literals(model, pop, hint.field_fact_id, owner_iid):
                        has.append(f"has {typedb.field_attribute_label(model, hint.field_fact_id)} {typedb._typeql_literal(literal, vt.scalar_kind)}")
            if not any(isinstance(model.object_types[r.player_id], ValueType) for r in fact.roles):
                rel_head = f"{rel_var} isa {typedb.relation_label(model, fact.id)}, links ({', '.join(links)})"
            else:
                # TypeDB relations may combine role players and owned value attributes.
                rel_head = f"{rel_var} isa {typedb.relation_label(model, fact.id)}, links ({', '.join(links)})" if links else f"{rel_var} isa {typedb.relation_label(model, fact.id)}"
            clauses.append(", ".join([rel_head, *has]))

    # Standalone ValueType populations have no global TypeDB attribute type in the
    # current projection: occurrence-specific attribute types are emitted instead.
    used_values = {iid for rows in pop.facts.values() for row in rows for iid in row}
    for iid in sorted(pop.values):
        if iid not in used_values:
            problems.append(f"standalone source value instance {iid} has no occurrence-specific TypeDB attribute target; contextual embedding is required")

    if problems:
        return LoweringProgram("typedb", "unsupported", "source_population_requires_context_or_identity", limitations=tuple(sorted(set(problems))))
    query = "insert\n" + ";\n".join(f"  {c}" for c in clauses) + ";\n" if clauses else "# empty source population: no TypeDB insert operations\n"
    return LoweringProgram(
        "typedb", "lowered", "exact_source_atom_projection",
        operations=({"op": "typeql", "transaction": "write_commit", "typeql": query},),
        notes=("All explicit object memberships share deterministic variables across relation clauses; duplicate conceptual fact rows remain distinct relation variables.",),
    )


def lower_population(model: Model, pop: SemanticPopulation, target: str) -> LoweringProgram:
    target = target.lower()
    if target == "postgres":
        return lower_postgres(model, pop)
    if target in {"mongo", "mongodb"}:
        return lower_mongo(model, pop)
    if target == "typedb":
        return lower_typedb(model, pop)
    return LoweringProgram(target, "unsupported", "unknown_target", limitations=(f"no shared-witness lowerer exists for target {target}",))


def render_program(program: LoweringProgram) -> str:
    if program.target == "postgres":
        return "\n".join(op["sql"] for op in program.operations if op.get("op") == "sql") + ("\n" if program.operations else "")
    if program.target == "typedb":
        return "\n".join(op["typeql"].rstrip() for op in program.operations if op.get("op") == "typeql") + ("\n" if program.operations else "")
    if program.target == "mongo":
        return json.dumps([op for op in program.operations], indent=2, sort_keys=True) + "\n"
    return json.dumps(program.to_dict(), indent=2, sort_keys=True) + "\n"
