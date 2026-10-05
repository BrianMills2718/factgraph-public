from __future__ import annotations

import re
from datetime import date, datetime
from uuid import UUID

from .ast import FieldDecl, ModelAst, RoleSequenceDecl
from .model import (
    Constraint,
    ConstraintKind,
    EntityType,
    FactType,
    FieldProjectionHint,
    Model,
    ObjectifiedFactType,
    Reading,
    SampleFact,
    SourceNote,
    ValueType,
)


class NormalizeError(ValueError):
    pass


_BUILTIN_SCALARS = {"String", "Int", "Decimal", "Date", "UUID", "Bool", "Timestamp", "Float"}
_NUMERIC_SCALARS = {"Int", "Decimal", "Float"}


def _literal_matches_scalar(value, scalar_kind: str) -> bool:
    if scalar_kind == "Int":
        return isinstance(value, int) and not isinstance(value, bool)
    if scalar_kind in {"Decimal", "Float"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if scalar_kind == "Bool":
        return isinstance(value, bool)
    if scalar_kind == "String":
        return isinstance(value, str)
    if not isinstance(value, str):
        return False
    try:
        if scalar_kind == "Date":
            date.fromisoformat(value)
            return True
        if scalar_kind == "Timestamp":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        if scalar_kind == "UUID":
            UUID(value)
            return True
    except ValueError:
        return False
    return False


def _add_note(model: Model, element_id: str, line: int) -> None:
    model.source_notes[element_id] = SourceNote(element_id, line)


def _field_fact_name(owner_name: str, field_name: str) -> str:
    return f"{owner_name}__{field_name}"


def _normalize_field(model: Model, owner_id: str, owner_name: str, field: FieldDecl) -> str:
    try:
        value_type = model.object_type_by_name(field.type_name)
    except KeyError as exc:
        raise NormalizeError(
            f"line {field.line}: field {owner_name}.{field.name} references unknown type {field.type_name!r}"
        ) from exc
    if not isinstance(value_type, ValueType):
        raise NormalizeError(
            f"line {field.line}: field {owner_name}.{field.name} must reference a value type, got {field.type_name!r}"
        )
    fact_name = _field_fact_name(owner_name, field.name)
    roles = (
        (
            ("owner", owner_id, f"{field.identity}/owner"),
            ("value", value_type.id, f"{field.identity}/value"),
        )
        if field.identity is not None
        else (("owner", owner_id), ("value", value_type.id))
    )
    fact = FactType.create(fact_name, roles, identity=field.identity)
    if fact.id in model.fact_types:
        raise NormalizeError(f"line {field.line}: generated field fact collision {fact_name!r}")
    model.fact_types[fact.id] = fact
    _add_note(model, fact.id, field.line)
    unique = Constraint.uniqueness(fact, ("owner",))
    model.constraints[unique.id] = unique
    if field.required or field.identifier:
        mandatory = Constraint.mandatory(fact, "owner")
        model.constraints[mandatory.id] = mandatory
    reading = Reading.create(
        fact,
        f"{{owner}} has {field.name} {{value}}",
        (fact.role("owner").id, fact.role("value").id),
    )
    model.readings[reading.id] = reading
    model.field_hints[fact.id] = FieldProjectionHint(
        owner_id,
        fact.id,
        field.name,
        value_type.id,
        field.required or field.identifier,
        field.identifier,
    )
    return fact.id


def _role_sequence(model: Model, decl: RoleSequenceDecl) -> tuple[FactType, tuple[str, ...]]:
    try:
        fact = model.fact_by_name(decl.fact_name)
    except KeyError as exc:
        raise NormalizeError(f"line {decl.line}: unknown fact {decl.fact_name!r} in role sequence") from exc
    ids: list[str] = []
    for name in decl.role_names:
        try:
            ids.append(fact.role(name).id)
        except KeyError as exc:
            raise NormalizeError(
                f"line {decl.line}: fact {decl.fact_name!r} has no role {name!r}"
            ) from exc
    return fact, tuple(ids)


def _role_player(model: Model, fact: FactType, role_id: str) -> str:
    return next(r.player_id for r in fact.roles if r.id == role_id)


def _compatible_players(model: Model, left: str, right: str, kind: ConstraintKind) -> bool:
    if left == right:
        return True
    if kind == ConstraintKind.SUBSET:
        return model.is_subtype(left, right)
    if kind == ConstraintKind.EXCLUSION:
        return model.is_subtype(left, right) or model.is_subtype(right, left)
    return False


def normalize_model(ast: ModelAst) -> Model:
    model = Model.create(ast.name, ast.identity)

    # Value types first.
    seen_names: set[str] = set()
    value_decls = {v.name: v for v in ast.values}
    for decl in ast.values:
        if decl.name in seen_names:
            raise NormalizeError(f"line {decl.line}: duplicate object/value type name {decl.name!r}")
        if decl.scalar_kind not in _BUILTIN_SCALARS:
            raise NormalizeError(
                f"line {decl.line}: unknown scalar kind {decl.scalar_kind!r}; expected one of {sorted(_BUILTIN_SCALARS)}"
            )
        obj = ValueType.create(decl.name, decl.scalar_kind, decl.identity)
        if obj.id in model.object_types:
            raise NormalizeError(f"line {decl.line}: duplicate semantic identity {obj.id!r}")
        model.object_types[obj.id] = obj
        _add_note(model, obj.id, decl.line)
        seen_names.add(decl.name)
        for raw in decl.constraints:
            if raw.kind == "range":
                if decl.scalar_kind not in _NUMERIC_SCALARS:
                    raise NormalizeError(
                        f"line {raw.line}: range() currently requires Int, Decimal, or Float value type"
                    )
                lo, hi = raw.args
                if not isinstance(lo, (int, float)) or isinstance(lo, bool) or not isinstance(hi, (int, float)) or isinstance(hi, bool):
                    raise NormalizeError(f"line {raw.line}: range bounds must be numeric")
                if lo > hi:
                    raise NormalizeError(f"line {raw.line}: range minimum exceeds maximum")
                c = Constraint.value(obj.id, {"kind": "range", "min": lo, "max": hi})
            elif raw.kind == "oneof":
                if not raw.args:
                    raise NormalizeError(f"line {raw.line}: oneof() must contain at least one value")
                bad = [v for v in raw.args if not _literal_matches_scalar(v, decl.scalar_kind)]
                if bad:
                    raise NormalizeError(
                        f"line {raw.line}: oneof() values {bad!r} are incompatible with scalar kind {decl.scalar_kind}"
                    )
                if len(set(raw.args)) != len(raw.args):
                    raise NormalizeError(f"line {raw.line}: oneof() values must be unique")
                c = Constraint.value(obj.id, {"kind": "oneof", "values": list(raw.args)})
            else:
                raise NormalizeError(f"line {raw.line}: unsupported value constraint {raw.kind!r}")
            model.constraints[c.id] = c
            _add_note(model, c.id, raw.line)

    # Entities before facts so facts can reference them.
    entity_decls = {e.name: e for e in ast.entities}
    if len(entity_decls) != len(ast.entities):
        raise NormalizeError("duplicate entity type name")
    for decl in ast.entities:
        if decl.name in seen_names:
            raise NormalizeError(f"line {decl.line}: duplicate object/value type name {decl.name!r}")
        obj = EntityType.create(decl.name, decl.identity)
        if obj.id in model.object_types:
            raise NormalizeError(f"line {decl.line}: duplicate semantic identity {obj.id!r}")
        model.object_types[obj.id] = obj
        _add_note(model, obj.id, decl.line)
        seen_names.add(decl.name)

    # Subtyping is semantic and is established before role-sequence constraints.
    seen_subtypes: set[str] = set()
    for decl in ast.subtypes:
        try:
            sub = model.object_type_by_name(decl.subtype_name)
            sup = model.object_type_by_name(decl.supertype_name)
        except KeyError as exc:
            raise NormalizeError(f"line {decl.line}: subtype declaration references unknown entity type") from exc
        if not isinstance(sub, EntityType) or not isinstance(sup, EntityType):
            raise NormalizeError(f"line {decl.line}: v0.3 subtyping currently supports entity types only")
        if sub.id == sup.id:
            raise NormalizeError(f"line {decl.line}: an entity cannot be its own supertype")
        if sub.id in seen_subtypes:
            raise NormalizeError(f"line {decl.line}: v0.3 allows one direct supertype per entity")
        if any(f.identifier for f in entity_decls[decl.subtype_name].fields):
            raise NormalizeError(
                f"line {decl.line}: subtype {decl.subtype_name} inherits identity from {decl.supertype_name}; do not redeclare id fields"
            )
        c = Constraint.subtype(sub.id, sup.id)
        model.constraints[c.id] = c
        _add_note(model, c.id, decl.line)
        seen_subtypes.add(sub.id)

    # Detect subtype cycles early.
    for entity in (o for o in model.object_types.values() if isinstance(o, EntityType)):
        cur = entity.id
        seen: set[str] = set()
        while True:
            if cur in seen:
                raise NormalizeError(f"subtype cycle involving {entity.name}")
            seen.add(cur)
            c = model.subtype_constraint(cur)
            if c is None or c.supertype_id is None:
                break
            cur = c.supertype_id

    # Reserve objectifications before fact role resolution so later facts may refer to them.
    fact_decls = {f.name: f for f in ast.facts}
    if len(fact_decls) != len(ast.facts):
        raise NormalizeError("duplicate fact type name")
    for decl in ast.facts:
        if decl.objectify_name is not None or decl.fields:
            obj_name = decl.objectify_name or f"{decl.name}Record"
            if obj_name in seen_names:
                raise NormalizeError(
                    f"line {decl.line}: objectification name {obj_name!r} collides with another object type"
                )
            fact_type_id = FactType.create(decl.name, (), identity=decl.identity).id
            objectify_identity = decl.objectify_identity
            if objectify_identity is None and decl.objectify_name is None and decl.identity is not None:
                objectify_identity = f"{decl.identity}/objectification"
            obj = ObjectifiedFactType.create(
                obj_name,
                fact_type_id=fact_type_id,
                identity=objectify_identity,
            )
            if obj.id in model.object_types:
                raise NormalizeError(f"line {decl.line}: duplicate semantic identity {obj.id!r}")
            model.object_types[obj.id] = obj
            _add_note(model, obj.id, decl.line)
            seen_names.add(obj_name)

    # Main fact types.
    for decl in ast.facts:
        if decl.name in seen_names:
            raise NormalizeError(f"line {decl.line}: fact name {decl.name!r} collides with an object/value type")
        role_pairs: list[tuple[str, str, str | None]] = []
        role_names: set[str] = set()
        for role in decl.roles:
            if role.name in role_names:
                raise NormalizeError(f"line {role.line}: duplicate role {role.name!r} in fact {decl.name}")
            role_names.add(role.name)
            try:
                player = model.object_type_by_name(role.type_name)
            except KeyError as exc:
                raise NormalizeError(
                    f"line {role.line}: fact {decl.name} role {role.name} references unknown type {role.type_name!r}"
                ) from exc
            role_pairs.append((role.name, player.id, role.identity))
        fact = FactType.create(decl.name, role_pairs, identity=decl.identity)
        if fact.id in model.fact_types:
            raise NormalizeError(f"line {decl.line}: duplicate semantic identity {fact.id!r}")
        role_ids = [r.id for r in fact.roles]
        if len(role_ids) != len(set(role_ids)):
            raise NormalizeError(f"line {decl.line}: duplicate role semantic identity in fact {decl.name!r}")
        model.fact_types[fact.id] = fact
        _add_note(model, fact.id, decl.line)

    # Entity fields -> fact types, then preferred identifiers.
    for decl in ast.entities:
        owner = model.object_type_by_name(decl.name)
        id_field_facts: list[str] = []
        field_names: set[str] = set()
        for field in decl.fields:
            if field.name in field_names:
                raise NormalizeError(f"line {field.line}: duplicate field {decl.name}.{field.name}")
            field_names.add(field.name)
            ffid = _normalize_field(model, owner.id, owner.name, field)
            if field.identifier:
                id_field_facts.append(ffid)
        if id_field_facts:
            c = Constraint.preferred_identifier(owner.id, id_field_facts)
            model.constraints[c.id] = c

    # Fact readings, local constraints, and relationship fields.
    for decl in ast.facts:
        fact = model.fact_by_name(decl.name)
        if decl.reading is not None:
            placeholders = tuple(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", decl.reading))
            unknown = [p for p in placeholders if p not in {r.name for r in fact.roles}]
            if unknown:
                raise NormalizeError(f"line {decl.line}: reading for {decl.name} uses unknown role(s) {unknown}")
            role_ids = tuple(fact.role(name).id for name in placeholders)
            reading = Reading.create(fact, decl.reading, role_ids)
            model.readings[reading.id] = reading

        for u in decl.uniques:
            for role_name in u.role_names:
                try:
                    fact.role(role_name)
                except KeyError as exc:
                    raise NormalizeError(
                        f"line {u.line}: unique() names unknown role {role_name!r} in {decl.name}"
                    ) from exc
            if not u.role_names:
                raise NormalizeError(f"line {u.line}: unique() needs at least one role")
            c = Constraint.uniqueness(fact, u.role_names)
            model.constraints[c.id] = c

        for mand in decl.mandatories:
            try:
                fact.role(mand.role_name)
            except KeyError as exc:
                raise NormalizeError(f"line {mand.line}: mandatory() names unknown role {mand.role_name!r}") from exc
            c = Constraint.mandatory(fact, mand.role_name)
            model.constraints[c.id] = c

        for freq in decl.frequencies:
            if not freq.role_names:
                raise NormalizeError(f"line {freq.line}: frequency() needs at least one role")
            if freq.min_frequency < 0 or freq.max_frequency < 0 or freq.min_frequency > freq.max_frequency:
                raise NormalizeError(f"line {freq.line}: invalid frequency bounds")
            for role_name in freq.role_names:
                try:
                    fact.role(role_name)
                except KeyError as exc:
                    raise NormalizeError(f"line {freq.line}: frequency() names unknown role {role_name!r}") from exc
            c = Constraint.frequency(fact, freq.role_names, freq.min_frequency, freq.max_frequency)
            model.constraints[c.id] = c
            _add_note(model, c.id, freq.line)

        for group in decl.unordered:
            if len(group.role_names) < 2:
                raise NormalizeError(f"line {group.line}: unordered() needs at least two roles")
            players = []
            for role_name in group.role_names:
                try:
                    players.append(fact.role(role_name).player_id)
                except KeyError as exc:
                    raise NormalizeError(f"line {group.line}: unordered() names unknown role {role_name!r}") from exc
            if len(set(players)) != 1:
                raise NormalizeError(
                    f"line {group.line}: unordered roles must have the same player type; got {group.role_names}"
                )
            c = Constraint.unordered(fact, group.role_names)
            model.constraints[c.id] = c

        for ring in decl.rings:
            if ring.kind == "symmetric":
                if len(fact.roles) != 2 or fact.roles[0].player_id != fact.roles[1].player_id:
                    raise NormalizeError(
                        f"line {ring.line}: symmetric ring constraint requires a binary fact whose roles share a player type"
                    )
            c = Constraint.ring(fact, ring.kind)
            model.constraints[c.id] = c

        if decl.fields:
            obj = model.objectification_for_fact(fact.id)
            assert obj is not None
            field_names: set[str] = set()
            for field in decl.fields:
                if field.name in field_names:
                    raise NormalizeError(f"line {field.line}: duplicate relationship field {decl.name}.{field.name}")
                field_names.add(field.name)
                _normalize_field(model, obj.id, obj.name, field)

    # Cross-fact subset/equality/exclusion constraints.
    kind_map = {
        "subset": ConstraintKind.SUBSET,
        "equality": ConstraintKind.EQUALITY,
        "exclusion": ConstraintKind.EXCLUSION,
    }
    for decl in ast.set_constraints:
        kind = kind_map[decl.kind]
        left_fact, left_ids = _role_sequence(model, decl.left)
        right_fact, right_ids = _role_sequence(model, decl.right)
        if len(left_ids) != len(right_ids):
            raise NormalizeError(f"line {decl.line}: {decl.kind} role sequences must have the same arity")
        for l, r in zip(left_ids, right_ids):
            lp = _role_player(model, left_fact, l)
            rp = _role_player(model, right_fact, r)
            if not _compatible_players(model, lp, rp, kind):
                raise NormalizeError(
                    f"line {decl.line}: incompatible role player types in {decl.kind} constraint"
                )
        c = Constraint.role_set(kind, left_fact, decl.left.role_names, right_fact, decl.right.role_names)
        model.constraints[c.id] = c
        _add_note(model, c.id, decl.line)

    # Samples refer only to declared source facts, not generated field facts.
    for sample in ast.samples:
        try:
            fact = model.fact_by_name(sample.fact_name)
        except KeyError as exc:
            raise NormalizeError(f"line {sample.line}: sample references unknown fact {sample.fact_name!r}") from exc
        if len(sample.values) != len(fact.roles):
            raise NormalizeError(
                f"line {sample.line}: sample for {sample.fact_name} has {len(sample.values)} values; expected {len(fact.roles)}"
            )
        model.samples.append(SampleFact(fact.id, sample.values, sample.line))

    for analysis in ast.analyses:
        model.analyses.append((analysis.name, analysis.args))

    dangling = model.dangling_references()
    if dangling:
        raise NormalizeError(
            "normalized model has dangling references: " + "; ".join(f"{r.element_id}: {r.message}" for r in dangling)
        )
    return model
