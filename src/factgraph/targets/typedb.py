from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import re
from typing import Any

from ..model import Constraint, ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType
from ..reporting import CapabilityEntry, CapabilityReport, CapabilityStatus


SCALAR_TYPEQL = {
    "String": "string",
    "Int": "integer",
    "Decimal": "decimal",
    "Date": "date",
    "UUID": "string",
    "Bool": "boolean",
    "Timestamp": "datetime-tz",
    "Float": "double",
}


_RESERVED = {
    "abstract", "alias", "and", "as", "assert", "attribute", "boolean", "card", "cascade",
    "contains", "date", "datetime", "decimal", "define", "delete", "distinct", "double", "entity",
    "false", "fetch", "fun", "has", "iid", "independent", "insert", "integer", "is", "isa", "key",
    "label", "let", "like", "links", "match", "not", "or", "owns", "plays", "put", "range",
    "redefine", "relation", "relates", "require", "return", "select", "string", "sub", "subkey",
    "true", "try", "undefine", "unique", "update", "value", "values", "with",
}


def label(text: str, *, prefix: str | None = None) -> str:
    """Deterministic TypeQL label with a namespace prefix to avoid cross-kind collisions."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "unnamed"
    if s[0].isdigit() or s in _RESERVED:
        s = "x-" + s
    if prefix:
        s = f"fg-{prefix}-{s}"
    return s


def entity_label(entity: EntityType) -> str:
    return label(entity.name, prefix="e")


def relation_label(model: Model, fact_id: str) -> str:
    return label(model.fact_types[fact_id].name, prefix="r")


def role_label(name: str) -> str:
    return label(name)


def field_attribute_label(model: Model, field_fact_id: str) -> str:
    hint = model.field_hints[field_fact_id]
    owner = model.object_types[hint.owner_object_type_id]
    owner_name = model.fact_types[owner.fact_type_id].name if isinstance(owner, ObjectifiedFactType) else owner.name
    return label(f"{owner_name}-{hint.field_name}", prefix="a")


def value_role_attribute_label(model: Model, fact_id: str, role_id: str) -> str:
    fact = model.fact_types[fact_id]
    role = next(r for r in fact.roles if r.id == role_id)
    return label(f"{fact.name}-{role.name}", prefix="a")


def _player_label(model: Model, player_id: str) -> str | None:
    player = model.object_types[player_id]
    if isinstance(player, EntityType):
        return entity_label(player)
    if isinstance(player, ObjectifiedFactType):
        return relation_label(model, player.fact_type_id)
    return None


def _value_constraint_is_used(model: Model, value_type_id: str) -> bool:
    if any(h.value_type_id == value_type_id for h in model.field_hints.values()):
        return True
    return any(
        r.player_id == value_type_id
        for f in model.fact_types.values()
        if f.id not in model.field_hints
        for r in f.roles
    )


def _typeql_literal(value: Any, scalar_kind: str) -> str:
    if scalar_kind == "String" or scalar_kind == "UUID":
        return json.dumps(str(value), ensure_ascii=False)
    if scalar_kind == "Bool":
        return "true" if bool(value) else "false"
    if scalar_kind == "Int":
        return str(int(value))
    if scalar_kind == "Float":
        return repr(float(value))
    if scalar_kind == "Decimal":
        # TypeQL distinguishes decimal from double with the `dec` suffix.
        d = Decimal(str(value))
        txt = format(d, "f")
        if "." not in txt:
            txt += ".0"
        return txt + "dec"
    if scalar_kind == "Date":
        return str(value)
    if scalar_kind == "Timestamp":
        text = str(value)
        # Factgraph canonical timestamps commonly use +00:00; TypeQL accepts ISO offsets.
        return text.replace("+00:00", "Z")
    raise ValueError(f"unsupported Factgraph scalar kind for TypeDB: {scalar_kind!r}")


def _value_annotations(model: Model, value_type_id: str) -> list[str]:
    value_type = model.object_types[value_type_id]
    assert isinstance(value_type, ValueType)
    out: list[str] = []
    for c in model.constraints_for_value(value_type_id):
        spec = c.value_spec or {}
        if spec.get("kind") == "oneof" and spec.get("values"):
            args = ", ".join(_typeql_literal(v, value_type.scalar_kind) for v in spec["values"])
            out.append(f"@values({args})")
        elif spec.get("kind") == "range":
            lo = _typeql_literal(spec["min"], value_type.scalar_kind)
            hi = _typeql_literal(spec["max"], value_type.scalar_kind)
            out.append(f"@range({lo}..{hi})")
    return out


def _identifier_constraints(model: Model) -> dict[str, Constraint]:
    return {
        c.object_type_id: c
        for c in model.constraints.values()
        if c.kind == ConstraintKind.PREFERRED_IDENTIFIER and c.object_type_id is not None
    }


def _direct_identifier_hints(model: Model, object_id: str):
    c = _identifier_constraints(model).get(object_id)
    if c is None:
        return []
    by_fact = model.field_hints
    return [by_fact[fid] for fid in c.field_fact_ids if fid in by_fact]


def _single_role_bound(model: Model, fact_id: str, role_id: str) -> tuple[int, int | None] | None:
    """Return a TypeDB plays-cardinality bound only where it is semantically safe.

    Factgraph frequency lower bounds describe multiplicity among *existing* projected
    fact keys. A TypeDB plays lower bound quantifies over every player instance, so
    lower bounds from frequency constraints are intentionally not translated here.
    Generic mandatory participation is translated separately and safely.
    """
    lower = 0
    upper: int | None = None
    touched = False
    for c in model.constraints_for_fact(fact_id):
        if c.kind == ConstraintKind.MANDATORY and c.role_ids == (role_id,):
            lower = max(lower, 1)
            touched = True
        elif c.kind == ConstraintKind.UNIQUENESS and c.role_ids == (role_id,):
            upper = 1 if upper is None else min(upper, 1)
            touched = True
        elif c.kind == ConstraintKind.FREQUENCY and c.role_ids == (role_id,) and c.min_frequency == 0:
            if c.max_frequency is not None:
                upper = c.max_frequency if upper is None else min(upper, c.max_frequency)
                touched = True
    return (lower, upper) if touched else None


def _card_annotation(lower: int, upper: int | None) -> str:
    if upper is not None and lower == upper:
        return f"@card({lower})"
    if upper is None:
        return f"@card({lower}..)"
    return f"@card({lower}..{upper})"


@dataclass(frozen=True)
class TypeDBAttribute:
    name: str
    value_type: str
    source_value_type_id: str
    source_element: str
    annotations: tuple[str, ...]


@dataclass(frozen=True)
class TypeDBCapability:
    kind: str  # owns | plays
    target: str
    annotations: tuple[str, ...]
    source_element: str


@dataclass(frozen=True)
class TypeDBObjectType:
    kind: str  # entity | relation
    name: str
    source_element: str
    supertype: str | None
    relates: tuple[tuple[str, tuple[str, ...], str], ...]
    capabilities: tuple[TypeDBCapability, ...]


@dataclass
class TypeDBPlan:
    attributes: list[TypeDBAttribute]
    objects: list[TypeDBObjectType]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "factgraph-typedb-plan-v1",
            "attributes": [asdict(a) for a in self.attributes],
            "objects": [asdict(o) for o in self.objects],
            "notes": [
                "Factgraph entity/objectified players map to TypeDB role players.",
                "Factgraph ValueType roles map to relation-owned attributes because TypeDB attributes cannot play roles.",
                "Relation tuple set semantics are not claimed: TypeDB may contain distinct relation instances with identical players unless an additional semantic mechanism is used.",
                "TypeDB 3.x @subkey is documented as planned/not available; compound Factgraph identifiers are therefore not claimed enforced.",
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def build_plan(model: Model) -> TypeDBPlan:
    attributes: list[TypeDBAttribute] = []
    objects: dict[str, TypeDBObjectType] = {}
    used_type_labels: dict[str, str] = {}

    def claim(lbl: str, source: str) -> None:
        prior = used_type_labels.get(lbl)
        if prior is not None and prior != source:
            raise ValueError(f"TypeDB target label collision: {lbl!r} for {prior!r} and {source!r}")
        used_type_labels[lbl] = source

    id_constraints = _identifier_constraints(model)

    # Occurrence-specific attributes: field projections and ValueType roles.
    for hint in sorted(model.field_hints.values(), key=lambda h: h.field_fact_id):
        vt = model.object_types[hint.value_type_id]
        assert isinstance(vt, ValueType)
        name = field_attribute_label(model, hint.field_fact_id)
        claim(name, hint.field_fact_id)
        attributes.append(TypeDBAttribute(
            name=name,
            value_type=SCALAR_TYPEQL[vt.scalar_kind],
            source_value_type_id=vt.id,
            source_element=hint.field_fact_id,
            annotations=tuple(_value_annotations(model, vt.id)),
        ))

    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            player = model.object_types[role.player_id]
            if not isinstance(player, ValueType):
                continue
            name = value_role_attribute_label(model, fact.id, role.id)
            claim(name, role.id)
            attributes.append(TypeDBAttribute(
                name=name,
                value_type=SCALAR_TYPEQL[player.scalar_kind],
                source_value_type_id=player.id,
                source_element=role.id,
                annotations=tuple(_value_annotations(model, player.id)),
            ))

    # Entity type declarations.
    for entity in sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.id):
        name = entity_label(entity)
        claim(name, entity.id)
        subtype = model.subtype_constraint(entity.id)
        supertype = None
        if subtype is not None:
            sup = model.object_types[subtype.supertype_id]
            if isinstance(sup, EntityType):
                supertype = entity_label(sup)

        caps: list[TypeDBCapability] = []
        direct_ids = _direct_identifier_hints(model, entity.id)
        direct_id_facts = {h.field_fact_id for h in direct_ids}
        single_id = len(direct_ids) == 1
        for hint in sorted((h for h in model.field_hints.values() if h.owner_object_type_id == entity.id), key=lambda h: h.field_fact_id):
            annotations: list[str] = []
            if hint.field_fact_id in direct_id_facts and single_id:
                annotations.append("@key")
            else:
                annotations.append("@card(1)" if hint.required else "@card(0..1)")
            caps.append(TypeDBCapability("owns", field_attribute_label(model, hint.field_fact_id), tuple(annotations), hint.field_fact_id))

        # Role-player capabilities. Multiple constraints combine into one @card.
        for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
            for role in sorted(fact.roles, key=lambda r: r.ordinal):
                if role.player_id != entity.id:
                    continue
                annotations: list[str] = []
                bound = _single_role_bound(model, fact.id, role.id)
                if bound is not None:
                    annotations.append(_card_annotation(*bound))
                caps.append(TypeDBCapability("plays", f"{relation_label(model, fact.id)}:{role_label(role.name)}", tuple(annotations), role.id))

        objects[name] = TypeDBObjectType("entity", name, entity.id, supertype, (), tuple(sorted(caps, key=lambda c: (c.kind, c.target, c.source_element))))

    # Relation declarations. These also represent objectified facts.
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        name = relation_label(model, fact.id)
        claim(name, fact.id)
        relates: list[tuple[str, tuple[str, ...], str]] = []
        caps: list[TypeDBCapability] = []

        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            player = model.object_types[role.player_id]
            if isinstance(player, ValueType):
                annotations = ["@card(1)"]
                # A single ValueType role uniqueness can be represented with @unique ownership.
                if any(c.kind == ConstraintKind.UNIQUENESS and c.role_ids == (role.id,) for c in model.constraints_for_fact(fact.id)):
                    annotations.append("@unique")
                if any(c.kind == ConstraintKind.FREQUENCY and c.role_ids == (role.id,) and c.min_frequency == 0 and c.max_frequency == 1 for c in model.constraints_for_fact(fact.id)):
                    if "@unique" not in annotations:
                        annotations.append("@unique")
                caps.append(TypeDBCapability("owns", value_role_attribute_label(model, fact.id, role.id), tuple(annotations), role.id))
            else:
                relates.append((role_label(role.name), ("@card(1)",), role.id))

        # Relationship fields on objectified facts become relation-owned attributes.
        obj = model.objectification_for_fact(fact.id)
        if obj is not None:
            idc = id_constraints.get(obj.id)
            id_facts = set(idc.field_fact_ids) if idc else set()
            single_id = len(id_facts) == 1
            for hint in sorted((h for h in model.field_hints.values() if h.owner_object_type_id == obj.id), key=lambda h: h.field_fact_id):
                annotations: list[str] = []
                if hint.field_fact_id in id_facts and single_id:
                    annotations.append("@key")
                else:
                    annotations.append("@card(1)" if hint.required else "@card(0..1)")
                caps.append(TypeDBCapability("owns", field_attribute_label(model, hint.field_fact_id), tuple(annotations), hint.field_fact_id))

        # Objectified relations can themselves play roles in other facts.
        if obj is not None:
            for outer in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
                for outer_role in sorted(outer.roles, key=lambda r: r.ordinal):
                    if outer_role.player_id != obj.id:
                        continue
                    annotations: list[str] = []
                    bound = _single_role_bound(model, outer.id, outer_role.id)
                    if bound is not None:
                        annotations.append(_card_annotation(*bound))
                    caps.append(TypeDBCapability("plays", f"{relation_label(model, outer.id)}:{role_label(outer_role.name)}", tuple(annotations), outer_role.id))

        objects[name] = TypeDBObjectType(
            "relation", name, fact.id, None,
            tuple(sorted(relates, key=lambda x: (x[0], x[2]))),
            tuple(sorted(caps, key=lambda c: (c.kind, c.target, c.source_element))),
        )

    return TypeDBPlan(
        attributes=sorted(attributes, key=lambda a: (a.name, a.source_element)),
        objects=sorted(objects.values(), key=lambda o: (o.kind, o.name, o.source_element)),
    )


def emit_schema(model: Model) -> str:
    plan = build_plan(model)
    lines = [
        "# generated by factgraph; deterministic TypeDB 3.x / TypeQL projection",
        "# semantic preservation claims are recorded separately in capabilities.json",
        "define",
    ]
    statements: list[str] = []
    for attr in plan.attributes:
        ann = (" " + " ".join(attr.annotations)) if attr.annotations else ""
        statements.append(f"attribute {attr.name} value {attr.value_type}{ann}")
    for obj in plan.objects:
        head = f"{obj.kind} {obj.name}"
        if obj.supertype:
            head += f" sub {obj.supertype}"
        chunks = [head]
        for role, anns, _source in obj.relates:
            suffix = (" " + " ".join(anns)) if anns else ""
            chunks.append(f"relates {role}{suffix}")
        for cap in obj.capabilities:
            suffix = (" " + " ".join(cap.annotations)) if cap.annotations else ""
            chunks.append(f"{cap.kind} {cap.target}{suffix}")
        if len(chunks) == 1:
            statements.append(chunks[0])
        else:
            statements.append(chunks[0] + ",\n    " + ",\n    ".join(chunks[1:]))
    for i, stmt in enumerate(statements):
        sep = ";" if i == len(statements) - 1 else ";"
        lines.append("  " + stmt.replace("\n", "\n  ") + sep)
    return "\n".join(lines) + "\n"


def _role_for_constraint(model: Model, c: Constraint):
    if c.fact_type_id is None or len(c.role_ids) != 1:
        return None
    fact = model.fact_types.get(c.fact_type_id)
    if fact is None:
        return None
    return next((r for r in fact.roles if r.id == c.role_ids[0]), None)


def capability_report(model: Model) -> CapabilityReport:
    entries: list[CapabilityEntry] = []
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.id):
        entries.append(CapabilityEntry(
            "fact_type", fact.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED,
            mechanism=f"TypeDB relation {relation_label(model, fact.id)} with first-class roles/value attributes",
            reason="TypeDB can store distinct relation instances with identical players/attributes; the canonical adapter does not impose tuple-level relation identity",
        ))
    for hint in sorted(model.field_hints.values(), key=lambda h: h.field_fact_id):
        entries.append(CapabilityEntry(
            "field_projection", hint.field_fact_id, "typedb", CapabilityStatus.NATIVE_ENFORCED,
            mechanism=f"owned attribute {field_attribute_label(model, hint.field_fact_id)} with cardinality at most one",
        ))

    identifiers = _identifier_constraints(model)
    for c in sorted(model.constraints.values(), key=lambda c: c.id):
        if c.kind == ConstraintKind.UNIQUENESS:
            if c.fact_type_id in model.field_hints:
                entries.append(CapabilityEntry("uniqueness", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "ownership @card(0..1) / @card(1) makes the field single-valued per owner"))
            elif len(c.role_ids) == 1:
                role = _role_for_constraint(model, c)
                player = model.object_types[role.player_id] if role is not None else None
                if isinstance(player, ValueType):
                    entries.append(CapabilityEntry("uniqueness", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "relation-owned attribute @unique"))
                else:
                    entries.append(CapabilityEntry("uniqueness", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "player `plays` capability @card(0..1)"))
            else:
                entries.append(CapabilityEntry("uniqueness", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="TypeDB 3.x has no available composite role-key annotation; @subkey is documented as planned/not yet available"))
        elif c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
            hints = [model.field_hints[f] for f in c.field_fact_ids if f in model.field_hints]
            if len(hints) == 1:
                entries.append(CapabilityEntry("preferred_identifier", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "single owned attribute @key"))
            else:
                entries.append(CapabilityEntry("preferred_identifier", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="compound @subkey is planned but not available in current TypeDB 3.x"))
        elif c.kind == ConstraintKind.MANDATORY:
            if c.fact_type_id in model.field_hints:
                entries.append(CapabilityEntry("mandatory", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "ownership @card(1) or @key"))
            else:
                role = _role_for_constraint(model, c)
                player = model.object_types[role.player_id] if role is not None else None
                if isinstance(player, (EntityType, ObjectifiedFactType)):
                    entries.append(CapabilityEntry("mandatory", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "player `plays relation:role @card(1..)` total-participation constraint"))
                else:
                    entries.append(CapabilityEntry("mandatory", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="ValueType roles map to relation-owned attributes; TypeDB has no free value population over which to quantify total participation"))
        elif c.kind == ConstraintKind.FREQUENCY:
            role = _role_for_constraint(model, c)
            if len(c.role_ids) == 1 and c.min_frequency == 0 and c.max_frequency is not None:
                player = model.object_types[role.player_id] if role is not None else None
                mechanism = "relation-owned attribute @unique" if isinstance(player, ValueType) and c.max_frequency == 1 else f"player `plays` @card(0..{c.max_frequency})"
                if isinstance(player, ValueType) and c.max_frequency != 1:
                    entries.append(CapabilityEntry("frequency", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="relation-owned value attributes can express single-value uniqueness but not arbitrary count bounds by attribute value across relation instances"))
                else:
                    entries.append(CapabilityEntry("frequency", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, mechanism))
            else:
                entries.append(CapabilityEntry("frequency", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="Factgraph frequency lower bounds quantify only over observed fact keys, while TypeDB `plays @card(min..max)` quantifies over every player instance; multi-role projection frequency also lacks a direct schema annotation"))
        elif c.kind == ConstraintKind.VALUE:
            if _value_constraint_is_used(model, c.object_type_id or ""):
                entries.append(CapabilityEntry("value", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "@values / @range on each generated occurrence-specific attribute type"))
            else:
                entries.append(CapabilityEntry("value", c.id, "typedb", CapabilityStatus.METADATA_ONLY, reason="constrained ValueType has no projected occurrence in this model"))
        elif c.kind == ConstraintKind.SUBTYPE:
            entries.append(CapabilityEntry("subtype", c.id, "typedb", CapabilityStatus.NATIVE_ENFORCED, "native entity subtyping with inherited capabilities"))
        elif c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
            entries.append(CapabilityEntry("unordered_roles", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="named roles remain ordered semantic positions; the adapter does not canonicalize role permutations into one relation identity"))
        elif c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
            entries.append(CapabilityEntry(c.kind.value, c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="cross-relation role-projection set constraints are not emitted as TypeDB schema constraints by the canonical adapter"))
        elif c.kind == ConstraintKind.RING:
            entries.append(CapabilityEntry(f"ring:{c.ring_kind}", c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="logical ring semantics are retained in the Factgraph audit but not compiled to a TypeDB schema constraint"))
        else:
            entries.append(CapabilityEntry(c.kind.value, c.id, "typedb", CapabilityStatus.REPRESENTED_NOT_ENFORCED, reason="constraint retained in semantic audit metadata"))

    for reading in sorted(model.readings.values(), key=lambda r: r.id):
        if reading.fact_type_id not in model.field_hints:
            entries.append(CapabilityEntry("reading", reading.id, "typedb", CapabilityStatus.METADATA_ONLY, reason="canonical TypeQL projection does not encode Factgraph natural-language readings"))
    return CapabilityReport("typedb", entries)


def emit_plan_json(model: Model) -> str:
    return build_plan(model).to_json()
