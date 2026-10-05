from __future__ import annotations

import json

from ..ids import slug
from ..model import ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType
from ..reporting import CapabilityEntry, CapabilityReport, CapabilityStatus


SCALAR_BSON = {
    "String": "string", "Int": "int", "Decimal": "decimal", "Date": "date",
    "UUID": "string", "Bool": "bool", "Timestamp": "date", "Float": "double",
}


def _hints(model: Model, owner_id: str):
    return sorted((h for h in model.field_hints.values() if h.owner_object_type_id == owner_id), key=lambda h: (not h.identifier_component, h.field_name))


def _direct_ids(model: Model, entity_id: str):
    return [h for h in _hints(model, entity_id) if h.identifier_component]


def _ids(model: Model, entity_id: str):
    direct = _direct_ids(model, entity_id)
    if direct:
        return direct
    sup = model.supertype_of(entity_id)
    if isinstance(sup, EntityType):
        return _ids(model, sup.id)
    return []


def _value_bson(model: Model, value_type_id: str) -> str:
    obj = model.object_types[value_type_id]
    assert isinstance(obj, ValueType)
    return SCALAR_BSON[obj.scalar_kind]




def _value_constraint_native(model: Model, constraint) -> bool:
    vt = model.object_types[constraint.object_type_id]
    assert isinstance(vt, ValueType)
    spec = constraint.value_spec or {}
    if spec.get("kind") == "range":
        # Plain JSON can carry bounds for integer/double validators without an
        # Extended-JSON/BSON conversion layer. Decimal128 is deliberately not
        # claimed here.
        return vt.scalar_kind in {"Int", "Float"}
    if spec.get("kind") == "oneof":
        # These canonical BSON representations are JSON-safe in the emitted
        # pure spec. Date/Timestamp require BSON Date instances; Decimal needs
        # Decimal128, so those remain explicit non-enforcement gaps.
        return vt.scalar_kind in {"String", "Int", "Float", "Bool", "UUID"}
    return False

def _value_schema(model: Model, value_type_id: str) -> dict:
    vt = model.object_types[value_type_id]
    assert isinstance(vt, ValueType)
    schema: dict = {"bsonType": SCALAR_BSON[vt.scalar_kind]}
    for c in model.constraints_for_value(vt.id):
        if not _value_constraint_native(model, c):
            continue
        spec = c.value_spec or {}
        if spec.get("kind") == "range":
            schema["minimum"] = spec["min"]
            schema["maximum"] = spec["max"]
        elif spec.get("kind") == "oneof":
            schema["enum"] = list(spec.get("values", []))
    return schema


def _role_properties(model: Model, role) -> dict[str, dict]:
    player = model.object_types[role.player_id]
    base = slug(role.name)
    if isinstance(player, ValueType):
        return {base: _value_schema(model, player.id)}
    if isinstance(player, EntityType):
        ids = _ids(model, player.id)
        if len(ids) == 1:
            prop = _value_schema(model, ids[0].value_type_id)
            prop["description"] = f"reference to {slug(player.name)}.{slug(ids[0].field_name)}"
            return {f"{base}_id": prop}
        if len(ids) > 1:
            out = {}
            for h in ids:
                prop = _value_schema(model, h.value_type_id)
                prop["description"] = f"component of composite reference to {slug(player.name)}"
                out[f"{base}__{slug(h.field_name)}"] = prop
            return out
        return {f"{base}_id": {"bsonType": "objectId", "description": f"reference to synthetic id of {slug(player.name)}"}}
    if isinstance(player, ObjectifiedFactType):
        fact = model.fact_types[player.fact_type_id]
        return {f"{base}_id": {"bsonType": "objectId", "description": f"reference to objectified fact {slug(fact.name)}._id"}}
    raise TypeError(player)


def _value_constraint_is_used(model: Model, value_type_id: str) -> bool:
    if any(h.value_type_id == value_type_id for h in model.field_hints.values()):
        return True
    return any(r.player_id == value_type_id for f in model.fact_types.values() if f.id not in model.field_hints for r in f.roles)


def build_plan(model: Model) -> dict:
    collections: list[dict] = []

    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.name):
        props: dict[str, dict] = {}
        required: list[str] = []
        indexes: list[dict] = []
        id_hints = _ids(model, entity.id)
        if not id_hints:
            props["_id"] = {"bsonType": "objectId"}
        else:
            # Inherited subtype identifiers are made explicit in the subtype collection.
            for h in id_hints:
                name = slug(h.field_name)
                props[name] = _value_schema(model, h.value_type_id)
                required.append(name)
        for h in _hints(model, entity.id):
            name = slug(h.field_name)
            if name not in props:
                props[name] = _value_schema(model, h.value_type_id)
            if h.required and name not in required:
                required.append(name)
        if id_hints:
            indexes.append({"keys": {slug(h.field_name): 1 for h in id_hints}, "unique": True, "purpose": "preferred/inherited identifier"})
        subtype = model.subtype_constraint(entity.id)
        collections.append({
            "name": slug(entity.name),
            "conceptual_kind": "subtype_entity" if subtype else "entity",
            "source_name": entity.name,
            "validator": {"$jsonSchema": {"bsonType": "object", "required": sorted(required), "properties": props}},
            "indexes": indexes,
        })

    source_facts = sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.name)
    for fact in source_facts:
        obj = model.objectification_for_fact(fact.id)
        props: dict[str, dict] = {}
        required: list[str] = []
        role_to_fields: dict[str, tuple[str, ...]] = {}
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            rprops = _role_properties(model, role)
            overlap = set(props).intersection(rprops)
            if overlap:
                raise ValueError(f"MongoDB field collision in fact {fact.name}: {sorted(overlap)}")
            role_to_fields[role.id] = tuple(rprops)
            props.update(rprops)
            required.extend(rprops)
        if obj is not None:
            props["_id"] = {"bsonType": "objectId"}
            for h in _hints(model, obj.id):
                name = slug(h.field_name)
                if name in props:
                    raise ValueError(f"MongoDB field collision in objectified fact {fact.name}: {name}")
                props[name] = _value_schema(model, h.value_type_id)
                if h.required:
                    required.append(name)

        validator: dict = {"$jsonSchema": {"bsonType": "object", "required": sorted(required), "properties": props}}
        exprs: list[dict] = []
        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNORDERED_ROLE_GROUP):
            groups = [role_to_fields[rid] for rid in c.role_ids]
            if all(len(g) == 1 for g in groups):
                names = [g[0] for g in groups]
                exprs.extend({"$lte": [f"${a}", f"${b}"]} for a, b in zip(names, names[1:]))
        if exprs:
            validator["$expr"] = exprs[0] if len(exprs) == 1 else {"$and": exprs}

        indexes: list[dict] = []
        full_fields = [field for role in sorted(fact.roles, key=lambda r: r.ordinal) for field in role_to_fields[role.id]]
        full_keys = {name: 1 for name in full_fields}
        if full_keys:
            indexes.append({"keys": full_keys, "unique": True, "purpose": "fact set semantics"})
        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNIQUENESS):
            fields = [field for rid in c.role_ids for field in role_to_fields[rid]]
            keys = {name: 1 for name in fields}
            if keys != full_keys:
                indexes.append({"keys": keys, "unique": True, "purpose": "declared uniqueness"})
        for c in model.constraints_for_fact(fact.id, ConstraintKind.FREQUENCY):
            if c.max_frequency == 1:
                fields = [field for rid in c.role_ids for field in role_to_fields[rid]]
                keys = {name: 1 for name in fields}
                if keys and not any(i["keys"] == keys and i.get("unique") for i in indexes):
                    indexes.append({"keys": keys, "unique": True, "purpose": "frequency max=1"})
        collections.append({
            "name": slug(fact.name),
            "conceptual_kind": "objectified_fact" if obj else "fact",
            "source_name": fact.name,
            "validator": validator,
            "indexes": indexes,
        })

    names = [c["name"] for c in collections]
    if len(names) != len(set(names)):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(f"MongoDB collection collision after normalization: {duplicates}")
    return {"format": "factgraph-mongo-plan-v1", "collections": collections}


def _pure_spec(plan: dict) -> dict:
    return {
        "format": "factgraph-mongo-target-v1",
        "collections": [
            {"name": c["name"], "validator": c["validator"], "indexes": [{"keys": i["keys"], "unique": bool(i.get("unique"))} for i in c["indexes"]]}
            for c in plan["collections"]
        ],
    }


def emit_plan_json(model: Model) -> str:
    return json.dumps(build_plan(model), indent=2, sort_keys=True) + "\n"


def emit_spec_json(model: Model) -> str:
    return json.dumps(_pure_spec(build_plan(model)), indent=2, sort_keys=True) + "\n"


def emit_script(model: Model) -> str:
    spec = _pure_spec(build_plan(model))
    lines = ["// generated by factgraph; deterministic canonical MongoDB projection", "// semantic recovery metadata is stored separately", ""]
    for coll in spec["collections"]:
        validator = json.dumps(coll["validator"], sort_keys=True, separators=(",", ":"))
        lines.append(f'db.createCollection({json.dumps(coll["name"])}, {{ validator: {validator} }});')
        for idx in coll["indexes"]:
            keys = json.dumps(idx["keys"], sort_keys=True, separators=(",", ":"))
            opts = json.dumps({"unique": bool(idx.get("unique"))}, sort_keys=True, separators=(",", ":"))
            lines.append(f'db.getCollection({json.dumps(coll["name"])}).createIndex({keys}, {opts});')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def capability_report(model: Model) -> CapabilityReport:
    entries: list[CapabilityEntry] = []
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        entries.append(CapabilityEntry("fact_type", fact.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "collection + compound unique index"))
    for hint in sorted(model.field_hints.values(), key=lambda h: h.field_fact_id):
        entries.append(CapabilityEntry("field_projection", hint.field_fact_id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "$jsonSchema property"))
    for c in sorted(model.constraints.values(), key=lambda c: c.id):
        if c.kind == ConstraintKind.UNIQUENESS:
            mechanism = "single-valued document property" if c.fact_type_id in model.field_hints else "unique index"
            entries.append(CapabilityEntry("uniqueness", c.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, mechanism))
        elif c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
            entries.append(CapabilityEntry("preferred_identifier", c.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "unique index"))
        elif c.kind == ConstraintKind.MANDATORY:
            if c.fact_type_id in model.field_hints:
                entries.append(CapabilityEntry("mandatory", c.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "$jsonSchema required"))
            else:
                entries.append(CapabilityEntry("mandatory", c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="total participation across collections is not a document-local validator rule"))
        elif c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
            fact = model.fact_types[c.fact_type_id]
            multi_component = any(len(_role_properties(model, next(r for r in fact.roles if r.id == rid))) != 1 for rid in c.role_ids)
            if multi_component:
                entries.append(CapabilityEntry("unordered_roles", c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="adapter does not generate tuple-order $expr for multi-component references"))
            else:
                entries.append(CapabilityEntry("unordered_roles", c.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "$expr canonical ordering"))
        elif c.kind == ConstraintKind.FREQUENCY:
            if c.max_frequency == 1:
                entries.append(CapabilityEntry("frequency", c.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "unique index over constrained role sequence"))
            else:
                entries.append(CapabilityEntry("frequency", c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="frequency bounds above one require cross-document counting"))
        elif c.kind == ConstraintKind.VALUE:
            if not _value_constraint_is_used(model, c.object_type_id):
                entries.append(CapabilityEntry("value", c.id, "mongo", CapabilityStatus.METADATA_ONLY, reason="constrained value type is not projected by this model"))
            elif _value_constraint_native(model, c):
                entries.append(CapabilityEntry("value", c.id, "mongo", CapabilityStatus.NATIVE_ENFORCED, "$jsonSchema enum/minimum/maximum using JSON-safe BSON values"))
            else:
                vt = model.object_types[c.object_type_id]
                entries.append(CapabilityEntry("value", c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason=f"{vt.scalar_kind} value constraint requires BSON-native literal encoding not carried by the canonical pure JSON target spec"))
        elif c.kind == ConstraintKind.SUBTYPE:
            entries.append(CapabilityEntry("subtype", c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="subtype collection reuses inherited identifier shape but MongoDB does not enforce cross-collection membership"))
        elif c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
            entries.append(CapabilityEntry(c.kind.value, c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="cross-collection role-sequence set constraint is not document-local"))
        elif c.kind == ConstraintKind.RING:
            entries.append(CapabilityEntry(f"ring:{c.ring_kind}", c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="requires cross-document companion-fact semantics"))
        else:
            entries.append(CapabilityEntry(c.kind.value, c.id, "mongo", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="retained in semantic manifest"))
    for reading in model.readings.values():
        if reading.fact_type_id not in model.field_hints:
            entries.append(CapabilityEntry("reading", reading.id, "mongo", CapabilityStatus.METADATA_ONLY, reason="conceptual reading is outside document validation"))
    return CapabilityReport("mongo", entries)


def structural_recovery(spec_json: str) -> dict:
    spec = json.loads(spec_json)
    if spec.get("format") != "factgraph-mongo-target-v1":
        raise ValueError("not a pure factgraph Mongo target spec")
    return {
        "reader_scope": "canonical pure Mongo target spec format v1 emitted by factgraph",
        "collections": spec["collections"],
        "recoverable_without_metadata": ["collection names", "field names/types", "required fields", "unique indexes", "document-local $expr/value constraints"],
        "not_reliably_recoverable_without_metadata": ["original conceptual type names in all renaming cases", "entity-vs-fact intent in all shapes", "readings", "non-enforced cross-collection semantics", "field sugar provenance", "objectification/subtype intent"],
    }


def structural_recovery_json(spec_json: str) -> str:
    return json.dumps(structural_recovery(spec_json), indent=2, sort_keys=True) + "\n"


def recover_with_manifest(spec_json: str, manifest_json: str) -> Model:
    structural_recovery(spec_json)
    return Model.from_manifest_json(manifest_json)
