from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable
import json

from .ids import resolved_id, stable_id


class ConstraintKind(str, Enum):
    UNIQUENESS = "uniqueness"
    MANDATORY = "mandatory"
    PREFERRED_IDENTIFIER = "preferred_identifier"
    UNORDERED_ROLE_GROUP = "unordered_role_group"
    RING = "ring"
    FREQUENCY = "frequency"
    VALUE = "value"
    SUBSET = "subset"
    EQUALITY = "equality"
    EXCLUSION = "exclusion"
    SUBTYPE = "subtype"


@dataclass(frozen=True)
class EntityType:
    id: str
    name: str

    @staticmethod
    def create(name: str, identity: str | None = None) -> "EntityType":
        return EntityType(resolved_id("entity", name, identity), name)


@dataclass(frozen=True)
class ValueType:
    id: str
    name: str
    scalar_kind: str

    @staticmethod
    def create(name: str, scalar_kind: str, identity: str | None = None) -> "ValueType":
        return ValueType(resolved_id("value", name, identity), name, scalar_kind)


@dataclass(frozen=True)
class ObjectifiedFactType:
    id: str
    name: str
    fact_type_id: str

    @staticmethod
    def create(
        name: str,
        *,
        fact_type_id: str,
        identity: str | None = None,
    ) -> "ObjectifiedFactType":
        """``fact_type_id`` must be the objectified fact's real id, never a name-derived guess."""
        if not isinstance(fact_type_id, str) or not fact_type_id:
            raise TypeError("ObjectifiedFactType.create requires the objectified fact's real id")
        return ObjectifiedFactType(
            resolved_id("objectified", name, identity),
            name,
            fact_type_id,
        )


ObjectType = EntityType | ValueType | ObjectifiedFactType


@dataclass(frozen=True)
class Role:
    id: str
    fact_type_id: str
    name: str
    player_id: str
    ordinal: int

    @staticmethod
    def create(
        fact_name: str,
        name: str,
        player_id: str,
        ordinal: int,
        *,
        fact_type_id: str,
        identity: str | None = None,
    ) -> "Role":
        # A role's own id may be name-derived; its owning fact reference may not.
        fact_id = fact_type_id
        role_id = (
            resolved_id("role", f"{fact_name}.{name}", identity)
            if identity is not None
            else stable_id("role", fact_name, name)
        )
        return Role(
            role_id,
            fact_id,
            name,
            player_id,
            ordinal,
        )


@dataclass(frozen=True)
class FactType:
    id: str
    name: str
    roles: tuple[Role, ...]

    @staticmethod
    def create(
        name: str,
        roles: Iterable[tuple[str, str] | tuple[str, str, str | None]],
        identity: str | None = None,
    ) -> "FactType":
        fact_id = resolved_id("fact", name, identity)
        normalized_roles = []
        for item in roles:
            if len(item) == 2:
                role_name, player_id = item
                role_identity = None
            else:
                role_name, player_id, role_identity = item
            normalized_roles.append((role_name, player_id, role_identity))
        role_objs = tuple(
            Role.create(
                name,
                role_name,
                player_id,
                idx,
                fact_type_id=fact_id,
                identity=role_identity,
            )
            for idx, (role_name, player_id, role_identity) in enumerate(normalized_roles)
        )
        return FactType(fact_id, name, role_objs)

    def role(self, name: str) -> Role:
        for role in self.roles:
            if role.name == name:
                return role
        raise KeyError(f"fact {self.name!r} has no role {name!r}")

    def role_ids(self, names: Iterable[str]) -> tuple[str, ...]:
        return tuple(self.role(n).id for n in names)

    def has_legacy_ids(self) -> bool:
        """True when the fact and all its roles use historical name-derived ids.

        Such facts keep name-based derived ids (constraints, readings) so
        v0.1-v0.7 manifests stay byte-stable.  Everything else derives from
        semantic ids.  Either way, *references* always use the real ids.
        """
        return self.id == stable_id("fact", self.name) and all(
            r.id == stable_id("role", self.name, r.name) for r in self.roles
        )


def _require_fact(fact: Any, factory: str) -> "FactType":
    if not isinstance(fact, FactType):
        raise TypeError(
            f"{factory} requires the FactType element, not {type(fact).__name__}; "
            "references must use the element's real id, never a name-derived guess"
        )
    return fact


@dataclass(frozen=True)
class Reading:
    id: str
    fact_type_id: str
    template: str
    role_ids: tuple[str, ...]

    @staticmethod
    def create(fact: FactType, template: str, role_ids: Iterable[str]) -> "Reading":
        fact = _require_fact(fact, "Reading.create")
        ids = tuple(role_ids)
        known = {r.id for r in fact.roles}
        unknown = [rid for rid in ids if rid not in known]
        if unknown:
            raise ValueError(f"reading for fact {fact.name!r} references unknown role id(s) {unknown}")
        reading_id = (
            stable_id("reading", fact.name, "canonical")
            if fact.id == stable_id("fact", fact.name)
            else stable_id("reading", fact.id, "canonical")
        )
        return Reading(reading_id, fact.id, template, ids)


@dataclass(frozen=True)
class Constraint:
    id: str
    kind: ConstraintKind
    fact_type_id: str | None = None
    role_ids: tuple[str, ...] = ()
    target_fact_type_id: str | None = None
    target_role_ids: tuple[str, ...] = ()
    object_type_id: str | None = None
    field_fact_ids: tuple[str, ...] = ()
    ring_kind: str | None = None
    min_frequency: int | None = None
    max_frequency: int | None = None
    value_spec: dict[str, Any] | None = None
    subtype_id: str | None = None
    supertype_id: str | None = None

    @staticmethod
    def _local(
        kind: ConstraintKind,
        tag: str,
        fact: FactType,
        role_names: tuple[str, ...],
        suffix: tuple[str, ...] = (),
        **extra: Any,
    ) -> "Constraint":
        role_ids = fact.role_ids(role_names)
        if fact.has_legacy_ids():
            cid = stable_id("constraint", tag, fact.name, *role_names, *suffix)
        else:
            cid = stable_id("constraint", tag, fact.id, *role_ids, *suffix)
        return Constraint(cid, kind, fact.id, role_ids, **extra)

    @staticmethod
    def uniqueness(fact: FactType, role_names: Iterable[str]) -> "Constraint":
        fact = _require_fact(fact, "Constraint.uniqueness")
        return Constraint._local(ConstraintKind.UNIQUENESS, "unique", fact, tuple(role_names))

    @staticmethod
    def mandatory(fact: FactType, role_name: str) -> "Constraint":
        fact = _require_fact(fact, "Constraint.mandatory")
        return Constraint._local(ConstraintKind.MANDATORY, "mandatory", fact, (role_name,))

    @staticmethod
    def frequency(fact: FactType, role_names: Iterable[str], minimum: int, maximum: int) -> "Constraint":
        fact = _require_fact(fact, "Constraint.frequency")
        return Constraint._local(
            ConstraintKind.FREQUENCY,
            "frequency",
            fact,
            tuple(role_names),
            (str(minimum), str(maximum)),
            min_frequency=minimum,
            max_frequency=maximum,
        )

    @staticmethod
    def unordered(fact: FactType, role_names: Iterable[str]) -> "Constraint":
        fact = _require_fact(fact, "Constraint.unordered")
        return Constraint._local(ConstraintKind.UNORDERED_ROLE_GROUP, "unordered", fact, tuple(role_names))

    @staticmethod
    def ring(fact: FactType, ring_kind: str) -> "Constraint":
        fact = _require_fact(fact, "Constraint.ring")
        anchor = fact.name if fact.has_legacy_ids() else fact.id
        return Constraint(
            stable_id("constraint", "ring", ring_kind, anchor),
            ConstraintKind.RING,
            fact.id,
            ring_kind=ring_kind,
        )

    @staticmethod
    def preferred_identifier(object_type_id: str, field_fact_ids: Iterable[str]) -> "Constraint":
        fields = tuple(field_fact_ids)
        return Constraint(
            stable_id("constraint", "identifier", object_type_id, *fields),
            ConstraintKind.PREFERRED_IDENTIFIER,
            object_type_id=object_type_id,
            field_fact_ids=fields,
        )

    @staticmethod
    def value(object_type_id: str, spec: dict[str, Any]) -> "Constraint":
        kind = str(spec.get("kind"))
        stable_parts = [kind, json.dumps(spec, sort_keys=True, separators=(",", ":"))]
        return Constraint(
            stable_id("constraint", "value", object_type_id, *stable_parts),
            ConstraintKind.VALUE,
            object_type_id=object_type_id,
            value_spec=spec,
        )

    @staticmethod
    def role_set(
        kind: ConstraintKind,
        left_fact: FactType,
        left_roles: Iterable[str],
        right_fact: FactType,
        right_roles: Iterable[str],
    ) -> "Constraint":
        if kind not in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
            raise ValueError(kind)
        left_fact = _require_fact(left_fact, "Constraint.role_set")
        right_fact = _require_fact(right_fact, "Constraint.role_set")
        left = tuple(left_roles)
        right = tuple(right_roles)
        left_ids = left_fact.role_ids(left)
        right_ids = right_fact.role_ids(right)
        if left_fact.has_legacy_ids() and right_fact.has_legacy_ids():
            cid = stable_id("constraint", kind.value, left_fact.name, *left, "to", right_fact.name, *right)
        else:
            cid = stable_id("constraint", kind.value, left_fact.id, *left_ids, "to", right_fact.id, *right_ids)
        return Constraint(
            cid,
            kind,
            fact_type_id=left_fact.id,
            role_ids=left_ids,
            target_fact_type_id=right_fact.id,
            target_role_ids=right_ids,
        )

    @staticmethod
    def subtype(subtype_id: str, supertype_id: str) -> "Constraint":
        return Constraint(
            stable_id("constraint", "subtype", subtype_id, supertype_id),
            ConstraintKind.SUBTYPE,
            subtype_id=subtype_id,
            supertype_id=supertype_id,
        )


@dataclass(frozen=True)
class SampleFact:
    fact_type_id: str
    values: tuple[str, ...]
    source_line: int | None = None


@dataclass(frozen=True)
class FieldProjectionHint:
    owner_object_type_id: str
    field_fact_id: str
    field_name: str
    value_type_id: str
    required: bool
    identifier_component: bool


@dataclass(frozen=True)
class SourceNote:
    element_id: str
    line: int


@dataclass(frozen=True)
class DanglingReference:
    """A reference from ``element_id`` to an id that does not exist in the model."""

    code: str
    element_id: str
    message: str


_FACT_LOCAL_KINDS = frozenset(
    {
        ConstraintKind.UNIQUENESS,
        ConstraintKind.MANDATORY,
        ConstraintKind.FREQUENCY,
        ConstraintKind.UNORDERED_ROLE_GROUP,
        ConstraintKind.RING,
    }
)
_ROLE_SET_KINDS = frozenset({ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION})


@dataclass
class Model:
    id: str
    name: str
    object_types: dict[str, ObjectType] = field(default_factory=dict)
    fact_types: dict[str, FactType] = field(default_factory=dict)
    readings: dict[str, Reading] = field(default_factory=dict)
    constraints: dict[str, Constraint] = field(default_factory=dict)
    samples: list[SampleFact] = field(default_factory=list)
    analyses: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    field_hints: dict[str, FieldProjectionHint] = field(default_factory=dict)
    source_notes: dict[str, SourceNote] = field(default_factory=dict)

    @staticmethod
    def create(name: str, identity: str | None = None) -> "Model":
        return Model(resolved_id("model", name, identity), name)

    def object_type_by_name(self, name: str) -> ObjectType:
        for obj in self.object_types.values():
            if obj.name == name:
                return obj
        raise KeyError(name)

    def fact_by_name(self, name: str) -> FactType:
        for fact in self.fact_types.values():
            if fact.name == name:
                return fact
        raise KeyError(name)

    def constraints_for_fact(self, fact_id: str, kind: ConstraintKind | None = None) -> list[Constraint]:
        out = [c for c in self.constraints.values() if c.fact_type_id == fact_id]
        if kind is not None:
            out = [c for c in out if c.kind == kind]
        return sorted(out, key=lambda c: c.id)

    def constraints_for_value(self, value_type_id: str) -> list[Constraint]:
        return sorted(
            (c for c in self.constraints.values() if c.kind == ConstraintKind.VALUE and c.object_type_id == value_type_id),
            key=lambda c: c.id,
        )

    def objectification_for_fact(self, fact_id: str) -> ObjectifiedFactType | None:
        for obj in self.object_types.values():
            if isinstance(obj, ObjectifiedFactType) and obj.fact_type_id == fact_id:
                return obj
        return None

    def subtype_constraint(self, subtype_id: str) -> Constraint | None:
        items = [c for c in self.constraints.values() if c.kind == ConstraintKind.SUBTYPE and c.subtype_id == subtype_id]
        if not items:
            return None
        return sorted(items, key=lambda c: c.id)[0]

    def supertype_of(self, subtype_id: str) -> ObjectType | None:
        c = self.subtype_constraint(subtype_id)
        return self.object_types.get(c.supertype_id) if c is not None else None

    def is_subtype(self, subtype_id: str, supertype_id: str) -> bool:
        cur = subtype_id
        seen: set[str] = set()
        while cur not in seen:
            if cur == supertype_id:
                return True
            seen.add(cur)
            c = self.subtype_constraint(cur)
            if c is None or c.supertype_id is None:
                return False
            cur = c.supertype_id
        return False

    def dangling_references(self) -> list[DanglingReference]:
        """Every cross-reference whose target id is absent from this model.

        This is the single referential-integrity definition; ``validate_model``
        reports it as diagnostics and ``build_audit`` refuses to run on it.
        """

        out: list[DanglingReference] = []

        def add(code: str, element_id: str, message: str) -> None:
            out.append(DanglingReference(code, element_id, message))

        def check_roles(code: str, element_id: str, fact_id: str | None, role_ids: Iterable[str], what: str) -> None:
            fact = self.fact_types.get(fact_id or "")
            if fact is None:
                return
            known = {r.id for r in fact.roles}
            bad = sorted(set(role_ids) - known)
            if bad:
                add(code, element_id, f"{what} references unknown role id(s) {bad} of fact {fact.id}")

        for oid in sorted(self.object_types):
            obj = self.object_types[oid]
            if isinstance(obj, ObjectifiedFactType) and obj.fact_type_id not in self.fact_types:
                add("OBJECTIFICATION_UNKNOWN_FACT", obj.id, f"objectification references missing fact {obj.fact_type_id}")

        for fid in sorted(self.fact_types):
            fact = self.fact_types[fid]
            for role in fact.roles:
                if role.player_id not in self.object_types:
                    add("UNKNOWN_PLAYER", role.id, f"role {role.name} references missing object type {role.player_id}")
                if role.fact_type_id != fact.id:
                    add("ROLE_WRONG_FACT", role.id, f"role {role.name} claims fact {role.fact_type_id} but belongs to {fact.id}")

        for rid in sorted(self.readings):
            reading = self.readings[rid]
            if reading.fact_type_id not in self.fact_types:
                add("READING_UNKNOWN_FACT", reading.id, f"reading references missing fact {reading.fact_type_id}")
            check_roles("READING_UNKNOWN_ROLE_ID", reading.id, reading.fact_type_id, reading.role_ids, "reading")

        for cid in sorted(self.constraints):
            c = self.constraints[cid]
            if c.kind in _FACT_LOCAL_KINDS or c.kind in _ROLE_SET_KINDS:
                if c.fact_type_id not in self.fact_types:
                    add("CONSTRAINT_UNKNOWN_FACT", c.id, f"constraint references missing fact {c.fact_type_id}")
                check_roles("CONSTRAINT_UNKNOWN_ROLE", c.id, c.fact_type_id, c.role_ids, "constraint")
            if c.kind in _ROLE_SET_KINDS:
                if c.target_fact_type_id not in self.fact_types:
                    add("SET_CONSTRAINT_UNKNOWN_TARGET", c.id, f"set constraint references missing target fact {c.target_fact_type_id}")
                check_roles("SET_CONSTRAINT_UNKNOWN_TARGET_ROLE", c.id, c.target_fact_type_id, c.target_role_ids, "set constraint target")
            if c.kind in {ConstraintKind.VALUE, ConstraintKind.PREFERRED_IDENTIFIER} and c.object_type_id not in self.object_types:
                add("CONSTRAINT_UNKNOWN_OBJECT", c.id, f"constraint references missing object type {c.object_type_id}")
            if c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
                for ffid in c.field_fact_ids:
                    if ffid not in self.fact_types:
                        add("IDENTIFIER_UNKNOWN_FIELD_FACT", c.id, f"identifier references missing field fact {ffid}")
            if c.kind == ConstraintKind.SUBTYPE:
                for ref in (c.subtype_id, c.supertype_id):
                    if ref not in self.object_types:
                        add("SUBTYPE_UNKNOWN_TYPE", c.id, f"subtype constraint references missing object type {ref}")

        for key in sorted(self.field_hints):
            hint = self.field_hints[key]
            if hint.field_fact_id not in self.fact_types:
                add("FIELD_HINT_UNKNOWN_FACT", key, f"field hint references missing fact {hint.field_fact_id}")
            for ref in (hint.owner_object_type_id, hint.value_type_id):
                if ref not in self.object_types:
                    add("FIELD_HINT_UNKNOWN_TYPE", key, f"field hint references missing object type {ref}")

        for sample in self.samples:
            if sample.fact_type_id not in self.fact_types:
                add("SAMPLE_UNKNOWN_FACT", sample.fact_type_id, f"sample (line {sample.source_line}) references missing fact {sample.fact_type_id}")
        return out

    def semantic_dict(self, include_samples: bool = False) -> dict[str, Any]:
        def obj_dict(obj: ObjectType) -> dict[str, Any]:
            if isinstance(obj, EntityType):
                return {"kind": "entity", "id": obj.id, "name": obj.name}
            if isinstance(obj, ValueType):
                return {"kind": "value", "id": obj.id, "name": obj.name, "scalar_kind": obj.scalar_kind}
            return {
                "kind": "objectified_fact",
                "id": obj.id,
                "name": obj.name,
                "fact_type_id": obj.fact_type_id,
            }

        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "object_types": [obj_dict(self.object_types[k]) for k in sorted(self.object_types)],
            "fact_types": [
                {
                    "id": f.id,
                    "name": f.name,
                    "roles": [asdict(r) for r in sorted(f.roles, key=lambda r: r.ordinal)],
                }
                for f in (self.fact_types[k] for k in sorted(self.fact_types))
            ],
            "readings": [asdict(self.readings[k]) for k in sorted(self.readings)],
            "constraints": [
                {**asdict(self.constraints[k]), "kind": self.constraints[k].kind.value}
                for k in sorted(self.constraints)
            ],
            "analyses": [list(x) for x in sorted(self.analyses)],
        }
        if include_samples:
            payload["samples"] = [asdict(s) for s in self.samples]
        return payload

    def semantic_json(self, include_samples: bool = False) -> str:
        return json.dumps(self.semantic_dict(include_samples=include_samples), indent=2, sort_keys=True) + "\n"

    def semantically_equal(self, other: "Model") -> bool:
        return self.semantic_dict(include_samples=False) == other.semantic_dict(include_samples=False)

    def manifest_dict(self) -> dict[str, Any]:
        payload = self.semantic_dict(include_samples=True)
        payload["field_hints"] = [asdict(self.field_hints[k]) for k in sorted(self.field_hints)]
        payload["source_notes"] = [asdict(self.source_notes[k]) for k in sorted(self.source_notes)]
        return payload

    def manifest_json(self) -> str:
        return json.dumps(self.manifest_dict(), indent=2, sort_keys=True) + "\n"

    @staticmethod
    def from_manifest_dict(data: dict[str, Any]) -> "Model":
        m = Model(data["id"], data["name"])
        for raw in data.get("object_types", []):
            if raw["kind"] == "entity":
                obj: ObjectType = EntityType(raw["id"], raw["name"])
            elif raw["kind"] == "value":
                obj = ValueType(raw["id"], raw["name"], raw["scalar_kind"])
            else:
                obj = ObjectifiedFactType(raw["id"], raw["name"], raw["fact_type_id"])
            m.object_types[obj.id] = obj
        for raw in data.get("fact_types", []):
            roles = tuple(Role(**r) for r in raw["roles"])
            f = FactType(raw["id"], raw["name"], roles)
            m.fact_types[f.id] = f
        for raw in data.get("readings", []):
            r = Reading(raw["id"], raw["fact_type_id"], raw["template"], tuple(raw["role_ids"]))
            m.readings[r.id] = r
        for raw in data.get("constraints", []):
            c = Constraint(
                id=raw["id"],
                kind=ConstraintKind(raw["kind"]),
                fact_type_id=raw.get("fact_type_id"),
                role_ids=tuple(raw.get("role_ids", [])),
                target_fact_type_id=raw.get("target_fact_type_id"),
                target_role_ids=tuple(raw.get("target_role_ids", [])),
                object_type_id=raw.get("object_type_id"),
                field_fact_ids=tuple(raw.get("field_fact_ids", [])),
                ring_kind=raw.get("ring_kind"),
                min_frequency=raw.get("min_frequency"),
                max_frequency=raw.get("max_frequency"),
                value_spec=raw.get("value_spec"),
                subtype_id=raw.get("subtype_id"),
                supertype_id=raw.get("supertype_id"),
            )
            m.constraints[c.id] = c
        for raw in data.get("samples", []):
            m.samples.append(SampleFact(raw["fact_type_id"], tuple(raw["values"]), raw.get("source_line")))
        for raw in data.get("analyses", []):
            m.analyses.append((raw[0], tuple(raw[1])))
        for raw in data.get("field_hints", []):
            h = FieldProjectionHint(**raw)
            m.field_hints[h.field_fact_id] = h
        for raw in data.get("source_notes", []):
            n = SourceNote(**raw)
            m.source_notes[n.element_id] = n
        return m

    @staticmethod
    def from_manifest_json(text: str) -> "Model":
        return Model.from_manifest_dict(json.loads(text))
