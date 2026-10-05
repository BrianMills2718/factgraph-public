from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from .model import (
    Constraint,
    ConstraintKind,
    EntityType,
    FactType,
    Model,
    ObjectifiedFactType,
    ValueType,
)


class ChangeSafety(str, Enum):
    SAFE = "safe"
    REQUIRES_DATA_CHECK = "requires_data_check"
    DESTRUCTIVE = "destructive"
    MANUAL = "manual"


class ChangeKind(str, Enum):
    RENAME_MODEL = "rename_model"
    ADD_OBJECT_TYPE = "add_object_type"
    DROP_OBJECT_TYPE = "drop_object_type"
    RENAME_OBJECT_TYPE = "rename_object_type"
    CHANGE_OBJECT_TYPE_KIND = "change_object_type_kind"
    CHANGE_VALUE_SCALAR = "change_value_scalar"
    ADD_FACT = "add_fact"
    DROP_FACT = "drop_fact"
    RENAME_FACT = "rename_fact"
    ADD_ROLE = "add_role"
    DROP_ROLE = "drop_role"
    RENAME_ROLE = "rename_role"
    CHANGE_ROLE_PLAYER = "change_role_player"
    REORDER_ROLE = "reorder_role"
    ADD_OBJECTIFICATION = "add_objectification"
    DROP_OBJECTIFICATION = "drop_objectification"
    RENAME_OBJECTIFICATION = "rename_objectification"
    ADD_FIELD = "add_field"
    DROP_FIELD = "drop_field"
    RENAME_FIELD = "rename_field"
    MOVE_FIELD = "move_field"
    CHANGE_FIELD_TYPE = "change_field_type"
    CHANGE_FIELD_REQUIRED = "change_field_required"
    CHANGE_FIELD_IDENTIFIER = "change_field_identifier"
    ADD_READING = "add_reading"
    DROP_READING = "drop_reading"
    CHANGE_READING = "change_reading"
    ADD_CONSTRAINT = "add_constraint"
    DROP_CONSTRAINT = "drop_constraint"
    CHANGE_CONSTRAINT = "change_constraint"
    ADD_SUBTYPE = "add_subtype"
    DROP_SUBTYPE = "drop_subtype"
    CHANGE_SUBTYPE = "change_subtype"


@dataclass(frozen=True)
class MigrationHints:
    object_types: dict[str, str]
    facts: dict[str, str]
    roles: dict[str, str]
    fields: dict[str, str]

    @staticmethod
    def empty() -> "MigrationHints":
        return MigrationHints({}, {}, {}, {})

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> "MigrationHints":
        data = data or {}
        return MigrationHints(
            {str(k): str(v) for k, v in data.get("object_types", {}).items()},
            {str(k): str(v) for k, v in data.get("facts", {}).items()},
            {str(k): str(v) for k, v in data.get("roles", {}).items()},
            {str(k): str(v) for k, v in data.get("fields", {}).items()},
        )

    @staticmethod
    def from_json(text: str) -> "MigrationHints":
        return MigrationHints.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_types": dict(sorted(self.object_types.items())),
            "facts": dict(sorted(self.facts.items())),
            "roles": dict(sorted(self.roles.items())),
            "fields": dict(sorted(self.fields.items())),
        }


@dataclass(frozen=True)
class SemanticChange:
    id: str
    kind: ChangeKind
    subject: str
    safety: ChangeSafety
    summary: str
    before: Any = None
    after: Any = None
    details: dict[str, Any] | None = None
    preconditions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["safety"] = self.safety.value
        return d


@dataclass
class SemanticDiff:
    before_model: str
    after_model: str
    changes: list[SemanticChange]
    hints: MigrationHints
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        by_safety = {s.value: 0 for s in ChangeSafety}
        by_kind: dict[str, int] = {}
        for c in self.changes:
            by_safety[c.safety.value] += 1
            by_kind[c.kind.value] = by_kind.get(c.kind.value, 0) + 1
        return {
            "format": "factgraph-semantic-diff-v1",
            "before_model": self.before_model,
            "after_model": self.after_model,
            "hints": self.hints.to_dict(),
            "warnings": self.warnings,
            "summary": {
                "change_count": len(self.changes),
                "by_safety": by_safety,
                "by_kind": dict(sorted(by_kind.items())),
                "has_destructive": by_safety[ChangeSafety.DESTRUCTIVE.value] > 0,
                "has_manual": by_safety[ChangeSafety.MANUAL.value] > 0,
                "requires_data_check": by_safety[ChangeSafety.REQUIRES_DATA_CHECK.value] > 0,
            },
            "changes": [c.to_dict() for c in self.changes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        d = self.to_dict()
        s = d["summary"]
        lines = [
            f"# Semantic diff — {self.before_model} → {self.after_model}",
            "",
            f"Total changes: **{s['change_count']}**.",
            "",
            "| Safety | Count |",
            "| --- | ---: |",
        ]
        for level in ChangeSafety:
            lines.append(f"| `{level.value}` | {s['by_safety'][level.value]} |")
        if self.warnings:
            lines += ["", "## Warnings", ""]
            for w in self.warnings:
                lines.append(f"- {w}")
        lines += ["", "## Changes", ""]
        if not self.changes:
            lines.append("No semantic changes.")
        for c in self.changes:
            lines += [
                f"### `{c.kind.value}` — {c.subject}",
                "",
                f"Safety: **`{c.safety.value}`**",
                "",
                c.summary,
            ]
            if c.preconditions:
                lines += ["", "Preconditions:"]
                for p in c.preconditions:
                    lines.append(f"- {p}")
            if c.before is not None or c.after is not None:
                lines += ["", "```json", json.dumps({"before": c.before, "after": c.after}, indent=2, sort_keys=True), "```"]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _digest(*values: Any) -> str:
    raw = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _change(
    kind: ChangeKind,
    subject: str,
    safety: ChangeSafety,
    summary: str,
    before: Any = None,
    after: Any = None,
    details: dict[str, Any] | None = None,
    preconditions: tuple[str, ...] = (),
) -> SemanticChange:
    cid = f"change:{kind.value}:{_digest(subject, before, after, details)}"
    return SemanticChange(cid, kind, subject, safety, summary, before, after, details, preconditions)


def _obj_kind(obj) -> str:
    if isinstance(obj, EntityType):
        return "entity"
    if isinstance(obj, ValueType):
        return "value"
    if isinstance(obj, ObjectifiedFactType):
        return "objectified_fact"
    raise TypeError(obj)


def _source_objects(model: Model) -> dict[str, EntityType | ValueType]:
    return {o.name: o for o in model.object_types.values() if isinstance(o, (EntityType, ValueType))}


def _object_snapshot(model: Model, obj: EntityType | ValueType) -> dict[str, Any]:
    base: dict[str, Any] = {"kind": _obj_kind(obj), "name": obj.name}
    if isinstance(obj, ValueType):
        base["scalar_kind"] = obj.scalar_kind
        base["value_constraints"] = [c.value_spec for c in model.constraints_for_value(obj.id)]
    return base


def _source_facts(model: Model) -> dict[str, FactType]:
    return {f.name: f for f in model.fact_types.values() if f.id not in model.field_hints}


def _objectification_by_fact(model: Model) -> dict[str, ObjectifiedFactType]:
    out: dict[str, ObjectifiedFactType] = {}
    for obj in model.object_types.values():
        if isinstance(obj, ObjectifiedFactType):
            fact = model.fact_types[obj.fact_type_id]
            out[fact.name] = obj
    return out


def _owner_name(model: Model, owner_id: str) -> str:
    return model.object_types[owner_id].name


def _field_records(model: Model) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for h in model.field_hints.values():
        owner = _owner_name(model, h.owner_object_type_id)
        typ = model.object_types[h.value_type_id].name
        out[(owner, h.field_name)] = {
            "owner": owner,
            "name": h.field_name,
            "type": typ,
            "required": h.required,
            "identifier": h.identifier_component,
            "field_fact_id": h.field_fact_id,
        }
    return out


def _validate_hints(before: Model, after: Model, hints: MigrationHints) -> None:
    bo, ao = _source_objects(before), _source_objects(after)
    bf, af = _source_facts(before), _source_facts(after)
    for old, new in hints.object_types.items():
        if old not in bo:
            raise ValueError(f"migration hint object_types references unknown before object {old!r}")
        if new not in ao:
            raise ValueError(f"migration hint object_types references unknown after object {new!r}")
    for old, new in hints.facts.items():
        if old not in bf:
            raise ValueError(f"migration hint facts references unknown before fact {old!r}")
        if new not in af:
            raise ValueError(f"migration hint facts references unknown after fact {new!r}")
    for old, new in hints.roles.items():
        if "." not in old or "." not in new:
            raise ValueError("role hints must use 'Fact.role' -> 'Fact.role'")
        of, orole = old.split(".", 1)
        nf, nrole = new.split(".", 1)
        if of not in bf or not any(r.name == orole for r in bf[of].roles):
            raise ValueError(f"migration hint roles references unknown before role {old!r}")
        if nf not in af or not any(r.name == nrole for r in af[nf].roles):
            raise ValueError(f"migration hint roles references unknown after role {new!r}")
    bfields, afields = _field_records(before), _field_records(after)
    for old, new in hints.fields.items():
        if "." not in old or "." not in new:
            raise ValueError("field hints must use 'Owner.field' -> 'Owner.field'")
        ob, fb = old.rsplit(".", 1)
        oa, fa = new.rsplit(".", 1)
        if (ob, fb) not in bfields:
            raise ValueError(f"migration hint fields references unknown before field {old!r}")
        if (oa, fa) not in afields:
            raise ValueError(f"migration hint fields references unknown after field {new!r}")


def _mapping(before_names: set[str], after_names: set[str], explicit: dict[str, str]) -> tuple[dict[str, str], set[str], set[str]]:
    mapping: dict[str, str] = {}
    used_after: set[str] = set()
    for old in sorted(before_names):
        if old in explicit:
            new = explicit[old]
            if new in used_after:
                raise ValueError(f"multiple before elements map to {new!r}")
            mapping[old] = new
            used_after.add(new)
        elif old in after_names and old not in used_after:
            mapping[old] = old
            used_after.add(old)
    dropped = before_names - set(mapping)
    added = after_names - used_after
    return mapping, dropped, added


def _element_mapping(
    before: dict[str, Any],
    after: dict[str, Any],
    explicit: dict[str, str],
) -> tuple[dict[str, str], set[str], set[str]]:
    """Align named elements by semantic ID before consulting names/hints.

    v0.8 stable identities make a rename an ordinary aligned element.  Legacy
    name-derived models retain the historical same-name behavior, and migration
    hints remain an escape hatch for old sources that renamed before identities
    were materialized.
    """

    mapping: dict[str, str] = {}
    used_after: set[str] = set()
    after_by_id = {obj.id: name for name, obj in after.items()}
    for old in sorted(before):
        target = after_by_id.get(before[old].id)
        if target is not None and target not in used_after:
            mapping[old] = target
            used_after.add(target)
    for old, new in sorted(explicit.items()):
        if old in mapping:
            if mapping[old] != new:
                raise ValueError(
                    f"migration hint for {old!r} conflicts with stable semantic identity mapping to {mapping[old]!r}"
                )
            continue
        if new in used_after:
            raise ValueError(f"multiple before elements map to {new!r}")
        mapping[old] = new
        used_after.add(new)
    for old in sorted(before):
        if old not in mapping and old in after and old not in used_after:
            mapping[old] = old
            used_after.add(old)
    return mapping, set(before) - set(mapping), set(after) - used_after


def _mapped_object_name(name: str, obj_map: dict[str, str], before: Model, after: Model, fact_map: dict[str, str]) -> str:
    if name in obj_map:
        return obj_map[name]
    # Objectified fact types are not in the primary object map; map them through their fact when possible.
    bobj = next((o for o in before.object_types.values() if o.name == name), None)
    if isinstance(bobj, ObjectifiedFactType):
        bfact = before.fact_types[bobj.fact_type_id]
        mapped_fact = fact_map.get(bfact.name)
        if mapped_fact:
            aobj = after.objectification_for_fact(after.fact_by_name(mapped_fact).id)
            if aobj is not None:
                return aobj.name
    return name


def _role_mapping(
    before_fact: FactType,
    after_fact: FactType,
    hints: MigrationHints,
) -> tuple[dict[str, str], set[str], set[str]]:
    explicit: dict[str, str] = {}
    for oldq, newq in hints.roles.items():
        of, orole = oldq.split(".", 1)
        nf, nrole = newq.split(".", 1)
        if of == before_fact.name and nf == after_fact.name:
            explicit[orole] = nrole
    before = {r.name: r for r in before_fact.roles}
    after = {r.name: r for r in after_fact.roles}
    return _element_mapping(before, after, explicit)


def _field_mapping(
    before: Model,
    after: Model,
    obj_map: dict[str, str],
    fact_map: dict[str, str],
    hints: MigrationHints,
) -> tuple[dict[tuple[str, str], tuple[str, str]], set[tuple[str, str]], set[tuple[str, str]]]:
    bf, af = _field_records(before), _field_records(after)
    mapping: dict[tuple[str, str], tuple[str, str]] = {}
    used_after: set[tuple[str, str]] = set()
    explicit: dict[tuple[str, str], tuple[str, str]] = {}
    for oldq, newq in hints.fields.items():
        ob, fb = oldq.rsplit(".", 1)
        oa, fa = newq.rsplit(".", 1)
        explicit[(ob, fb)] = (oa, fa)
    after_by_id = {v["field_fact_id"]: key for key, v in af.items()}
    for key in sorted(bf):
        if key in explicit:
            target = explicit[key]
        elif bf[key]["field_fact_id"] in after_by_id:
            target = after_by_id[bf[key]["field_fact_id"]]
        else:
            owner, field = key
            mapped_owner = _mapped_object_name(owner, obj_map, before, after, fact_map)
            target = (mapped_owner, field)
        if target in af and target not in used_after:
            mapping[key] = target
            used_after.add(target)
    return mapping, set(bf) - set(mapping), set(af) - used_after


def _constraint_role_names(model: Model, fact_id: str | None, role_ids: tuple[str, ...]) -> tuple[str, ...]:
    if fact_id is None:
        return ()
    fact = model.fact_types[fact_id]
    by_id = {r.id: r.name for r in fact.roles}
    return tuple(by_id[r] for r in role_ids)


def _constraint_records(
    model: Model,
    *,
    object_mapper=lambda x: x,
    fact_mapper=lambda x: x,
    role_mapper=lambda f, r: r,
) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Canonical user-level constraint records.

    Field-generated uniqueness/mandatory and preferred identifiers are represented
    by field records instead of duplicated here. Subtyping and value constraints
    get dedicated comparison logic elsewhere.
    """
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for c in model.constraints.values():
        if c.kind in {ConstraintKind.PREFERRED_IDENTIFIER, ConstraintKind.VALUE, ConstraintKind.SUBTYPE}:
            continue
        if c.fact_type_id in model.field_hints:
            continue
        if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
            lf = model.fact_types[c.fact_type_id]
            rf = model.fact_types[c.target_fact_type_id]
            lfn, rfn = fact_mapper(lf.name), fact_mapper(rf.name)
            lroles = tuple(role_mapper(lf.name, x) for x in _constraint_role_names(model, c.fact_type_id, c.role_ids))
            rroles = tuple(role_mapper(rf.name, x) for x in _constraint_role_names(model, c.target_fact_type_id, c.target_role_ids))
            key = (c.kind.value, lfn, lroles, rfn, rroles)
            out[key] = {"kind": c.kind.value, "left_fact": lfn, "left_roles": lroles, "right_fact": rfn, "right_roles": rroles}
            continue
        fact = model.fact_types[c.fact_type_id]
        fn = fact_mapper(fact.name)
        roles = tuple(role_mapper(fact.name, x) for x in _constraint_role_names(model, c.fact_type_id, c.role_ids))
        if c.kind == ConstraintKind.FREQUENCY:
            key = (c.kind.value, fn, roles)
            out[key] = {"kind": c.kind.value, "fact": fn, "roles": roles, "min": c.min_frequency, "max": c.max_frequency}
        elif c.kind == ConstraintKind.RING:
            key = (c.kind.value, fn, c.ring_kind)
            out[key] = {"kind": c.kind.value, "fact": fn, "ring_kind": c.ring_kind}
        else:
            key = (c.kind.value, fn, roles)
            out[key] = {"kind": c.kind.value, "fact": fn, "roles": roles}
    return out


def _constraint_add_safety(record: dict[str, Any]) -> tuple[ChangeSafety, tuple[str, ...]]:
    kind = record["kind"]
    if kind in {"uniqueness", "mandatory", "unordered_role_group", "ring", "subset", "equality", "exclusion"}:
        return ChangeSafety.REQUIRES_DATA_CHECK, ("existing population must satisfy the newly declared constraint",)
    if kind == "frequency":
        return ChangeSafety.REQUIRES_DATA_CHECK, ("existing fact multiplicities must satisfy the new frequency bounds",)
    return ChangeSafety.REQUIRES_DATA_CHECK, ("existing population must be checked before enforcing the new constraint",)


def _frequency_change_safety(before: dict[str, Any], after: dict[str, Any]) -> tuple[ChangeSafety, tuple[str, ...]]:
    bmin, bmax = before["min"], before["max"]
    amin, amax = after["min"], after["max"]
    tighter = amin > bmin or amax < bmax
    if tighter:
        return ChangeSafety.REQUIRES_DATA_CHECK, ("existing fact multiplicities must satisfy the tighter bounds",)
    return ChangeSafety.SAFE, ()


def _value_change_safety(before: dict[str, Any] | None, after: dict[str, Any] | None) -> tuple[ChangeSafety, tuple[str, ...]]:
    if before is None and after is not None:
        return ChangeSafety.REQUIRES_DATA_CHECK, ("existing values must satisfy the new value domain",)
    if after is None:
        return ChangeSafety.SAFE, ()
    if before is None:
        return ChangeSafety.REQUIRES_DATA_CHECK, ()
    if before.get("kind") != after.get("kind"):
        return ChangeSafety.REQUIRES_DATA_CHECK, ("existing values must be checked because the value-domain form changed",)
    if before.get("kind") == "range":
        tighter = after["min"] > before["min"] or after["max"] < before["max"]
        return (ChangeSafety.REQUIRES_DATA_CHECK, ("existing values must satisfy the tighter range",)) if tighter else (ChangeSafety.SAFE, ())
    if before.get("kind") == "oneof":
        bs, aas = set(before.get("values", [])), set(after.get("values", []))
        return (ChangeSafety.REQUIRES_DATA_CHECK, ("existing values must belong to the reduced enumeration",)) if not bs.issubset(aas) else (ChangeSafety.SAFE, ())
    return ChangeSafety.REQUIRES_DATA_CHECK, ("existing values must be checked against the changed domain",)



@dataclass(frozen=True)
class ModelAlignment:
    object_types: dict[str, str]
    facts: dict[str, str]
    roles: dict[str, dict[str, str]]
    fields: dict[tuple[str, str], tuple[str, str]]


def align_models(before: Model, after: Model, hints: MigrationHints | None = None) -> ModelAlignment:
    hints = hints or MigrationHints.empty()
    _validate_hints(before, after, hints)
    bobjects, aobjects = _source_objects(before), _source_objects(after)
    obj_map, _, _ = _element_mapping(bobjects, aobjects, hints.object_types)
    bfacts, afacts = _source_facts(before), _source_facts(after)
    fact_map, _, _ = _element_mapping(bfacts, afacts, hints.facts)
    role_maps: dict[str, dict[str, str]] = {}
    for oldf, newf in sorted(fact_map.items()):
        rmap, _, _ = _role_mapping(bfacts[oldf], afacts[newf], hints)
        role_maps[oldf] = rmap
    field_map, _, _ = _field_mapping(before, after, obj_map, fact_map, hints)
    return ModelAlignment(obj_map, fact_map, role_maps, field_map)

def semantic_diff(before: Model, after: Model, hints: MigrationHints | None = None) -> SemanticDiff:
    hints = hints or MigrationHints.empty()
    _validate_hints(before, after, hints)
    changes: list[SemanticChange] = []
    warnings: list[str] = []

    if before.name != after.name:
        changes.append(_change(ChangeKind.RENAME_MODEL, before.name, ChangeSafety.SAFE, f"model renamed from {before.name} to {after.name}", before.name, after.name))

    bobjects, aobjects = _source_objects(before), _source_objects(after)
    obj_map, dropped_objects, added_objects = _element_mapping(bobjects, aobjects, hints.object_types)
    for old, new in sorted(obj_map.items()):
        bo, ao = bobjects[old], aobjects[new]
        if old != new:
            changes.append(_change(ChangeKind.RENAME_OBJECT_TYPE, old, ChangeSafety.SAFE, f"{_obj_kind(bo)} {old} renamed to {new}", old, new, {"before_kind": _obj_kind(bo), "after_kind": _obj_kind(ao)}))
        if _obj_kind(bo) != _obj_kind(ao):
            changes.append(_change(ChangeKind.CHANGE_OBJECT_TYPE_KIND, new, ChangeSafety.MANUAL, f"object type {old} changes kind from {_obj_kind(bo)} to {_obj_kind(ao)}", _obj_kind(bo), _obj_kind(ao)))
        elif isinstance(bo, ValueType) and isinstance(ao, ValueType) and bo.scalar_kind != ao.scalar_kind:
            changes.append(_change(ChangeKind.CHANGE_VALUE_SCALAR, new, ChangeSafety.MANUAL, f"value type {new} changes scalar kind", bo.scalar_kind, ao.scalar_kind, preconditions=("existing stored values require an explicit conversion strategy",)))
    for name in sorted(dropped_objects):
        obj = bobjects[name]
        changes.append(_change(ChangeKind.DROP_OBJECT_TYPE, name, ChangeSafety.DESTRUCTIVE, f"drop {_obj_kind(obj)} {name}", _object_snapshot(before, obj), None, preconditions=("confirm no retained data or references require this object type",)))
    for name in sorted(added_objects):
        obj = aobjects[name]
        changes.append(_change(ChangeKind.ADD_OBJECT_TYPE, name, ChangeSafety.SAFE, f"add {_obj_kind(obj)} {name}", None, _object_snapshot(after, obj)))

    bfacts, afacts = _source_facts(before), _source_facts(after)
    fact_map, dropped_facts, added_facts = _element_mapping(bfacts, afacts, hints.facts)
    for old, new in sorted(fact_map.items()):
        if old != new:
            changes.append(_change(ChangeKind.RENAME_FACT, old, ChangeSafety.SAFE, f"fact {old} renamed to {new}", old, new))
    for name in sorted(dropped_facts):
        changes.append(_change(ChangeKind.DROP_FACT, name, ChangeSafety.DESTRUCTIVE, f"drop fact {name} and its population", {"fact": name}, None, preconditions=("confirm the fact population may be discarded or migrated elsewhere",)))
    for name in sorted(added_facts):
        changes.append(_change(ChangeKind.ADD_FACT, name, ChangeSafety.SAFE, f"add fact {name}", None, {"fact": name}))

    # Role changes inside aligned facts.
    role_maps: dict[str, dict[str, str]] = {}
    for oldf, newf in sorted(fact_map.items()):
        bfact, afact = bfacts[oldf], afacts[newf]
        rmap, rdropped, radded = _role_mapping(bfact, afact, hints)
        role_maps[oldf] = rmap
        br = {r.name: r for r in bfact.roles}
        ar = {r.name: r for r in afact.roles}
        for oldr, newr in sorted(rmap.items()):
            brr, arr = br[oldr], ar[newr]
            subject = f"{newf}.{newr}"
            if oldr != newr:
                changes.append(_change(ChangeKind.RENAME_ROLE, f"{oldf}.{oldr}", ChangeSafety.SAFE, f"role {oldf}.{oldr} renamed to {newf}.{newr}", f"{oldf}.{oldr}", f"{newf}.{newr}"))
            mapped_player = _mapped_object_name(before.object_types[brr.player_id].name, obj_map, before, after, fact_map)
            after_player = after.object_types[arr.player_id].name
            if mapped_player != after_player:
                changes.append(_change(ChangeKind.CHANGE_ROLE_PLAYER, subject, ChangeSafety.MANUAL, f"role {subject} changes player type", mapped_player, after_player, preconditions=("define how existing role values are converted/referenced under the new player type",)))
            if brr.ordinal != arr.ordinal:
                changes.append(_change(ChangeKind.REORDER_ROLE, subject, ChangeSafety.SAFE, f"role {subject} changes presentation ordinal", brr.ordinal, arr.ordinal, details={"semantic_note": "role identity is name-based; ordinal is presentation/canonical projection order"}))
        for rname in sorted(rdropped):
            changes.append(_change(ChangeKind.DROP_ROLE, f"{oldf}.{rname}", ChangeSafety.MANUAL, f"drop role {oldf}.{rname} from fact {oldf}", {"role": rname}, None, preconditions=("fact arity changes require an explicit population rewrite",)))
        for rname in sorted(radded):
            changes.append(_change(ChangeKind.ADD_ROLE, f"{newf}.{rname}", ChangeSafety.MANUAL, f"add role {newf}.{rname} to fact {newf}", None, {"role": rname}, preconditions=("existing facts require a value for the new role or an explicit migration policy",)))

        bobj = before.objectification_for_fact(bfact.id)
        aobj = after.objectification_for_fact(afact.id)
        if bobj is None and aobj is not None:
            changes.append(_change(ChangeKind.ADD_OBJECTIFICATION, newf, ChangeSafety.MANUAL, f"fact {newf} becomes objectified as {aobj.name}", None, aobj.name, preconditions=("define identity for existing fact instances in every target",)))
        elif bobj is not None and aobj is None:
            changes.append(_change(ChangeKind.DROP_OBJECTIFICATION, oldf, ChangeSafety.MANUAL, f"fact {oldf} stops being objectified", bobj.name, None, preconditions=("verify no facts or fields reference the objectified identity",)))
        elif bobj is not None and aobj is not None and bobj.name != aobj.name:
            changes.append(_change(ChangeKind.RENAME_OBJECTIFICATION, bobj.name, ChangeSafety.SAFE, f"objectified fact type {bobj.name} renamed to {aobj.name}", bobj.name, aobj.name))

    # Fields are compared at the ergonomic/source level rather than as generated binary facts.
    bfield_records, afield_records = _field_records(before), _field_records(after)
    fmap, fdropped, fadded = _field_mapping(before, after, obj_map, fact_map, hints)
    for oldkey, newkey in sorted(fmap.items()):
        b, a = bfield_records[oldkey], afield_records[newkey]
        oldq, newq = f"{oldkey[0]}.{oldkey[1]}", f"{newkey[0]}.{newkey[1]}"
        mapped_owner = _mapped_object_name(oldkey[0], obj_map, before, after, fact_map)
        owner_equivalent = mapped_owner == newkey[0]
        if oldkey[1] != newkey[1] or not owner_equivalent:
            moved = not owner_equivalent
            kind = ChangeKind.MOVE_FIELD if moved else ChangeKind.RENAME_FIELD
            safety = ChangeSafety.MANUAL if moved else ChangeSafety.SAFE
            changes.append(_change(kind, oldq, safety, f"field {oldq} {'moves to' if moved else 'renamed to'} {newq}", oldq, newq, preconditions=(("moving a field requires a data-transfer policy",) if moved else ())))
        subject = newq
        mapped_type = _mapped_object_name(b["type"], obj_map, before, after, fact_map)
        if mapped_type != a["type"]:
            changes.append(_change(ChangeKind.CHANGE_FIELD_TYPE, subject, ChangeSafety.MANUAL, f"field {subject} changes value type", mapped_type, a["type"], preconditions=("define and verify a conversion for existing values",)))
        if b["required"] != a["required"]:
            if a["required"]:
                safety = ChangeSafety.REQUIRES_DATA_CHECK
                pre = ("existing records must contain a non-null/non-missing value before enforcing requiredness",)
            else:
                safety, pre = ChangeSafety.SAFE, ()
            changes.append(_change(ChangeKind.CHANGE_FIELD_REQUIRED, subject, safety, f"field {subject} requiredness changes", b["required"], a["required"], preconditions=pre))
        if b["identifier"] != a["identifier"]:
            changes.append(_change(ChangeKind.CHANGE_FIELD_IDENTIFIER, subject, ChangeSafety.MANUAL, f"field {subject} identifier participation changes", b["identifier"], a["identifier"], preconditions=("primary/reference identity migration must be planned explicitly",)))
    for owner, field in sorted(fdropped):
        q = f"{owner}.{field}"
        changes.append(_change(ChangeKind.DROP_FIELD, q, ChangeSafety.DESTRUCTIVE, f"drop field {q}", bfield_records[(owner, field)], None, preconditions=("confirm or archive existing field values before removal",)))
    for owner, field in sorted(fadded):
        q = f"{owner}.{field}"
        rec = afield_records[(owner, field)]
        safety = ChangeSafety.REQUIRES_DATA_CHECK if rec["required"] else ChangeSafety.SAFE
        pre = ("existing owner records need a value/backfill before requiredness can be enforced",) if rec["required"] else ()
        changes.append(_change(ChangeKind.ADD_FIELD, q, safety, f"add {'required' if rec['required'] else 'optional'} field {q}", None, rec, preconditions=pre))

    # Value-domain changes by mapped value type and constraint kind.
    for old, new in sorted(obj_map.items()):
        bo, ao = bobjects[old], aobjects[new]
        if not isinstance(bo, ValueType) or not isinstance(ao, ValueType):
            continue
        bv = {str((c.value_spec or {}).get("kind")): c.value_spec for c in before.constraints_for_value(bo.id)}
        av = {str((c.value_spec or {}).get("kind")): c.value_spec for c in after.constraints_for_value(ao.id)}
        for vk in sorted(set(bv) | set(av)):
            bspec, aspec = bv.get(vk), av.get(vk)
            if bspec == aspec:
                continue
            safety, pre = _value_change_safety(bspec, aspec)
            if bspec is None:
                kind, summary = ChangeKind.ADD_CONSTRAINT, f"add {vk} value constraint to {new}"
            elif aspec is None:
                kind, summary = ChangeKind.DROP_CONSTRAINT, f"remove {vk} value constraint from {new}"
            else:
                kind, summary = ChangeKind.CHANGE_CONSTRAINT, f"change {vk} value constraint on {new}"
            changes.append(_change(kind, f"{new}.value:{vk}", safety, summary, bspec, aspec, {"constraint_kind": "value", "value_type": new}, preconditions=pre))

    # Subtyping separately because it changes identity/table shape in the current targets.
    def subtype_map(model: Model, mapper) -> dict[str, str]:
        out = {}
        for c in model.constraints.values():
            if c.kind == ConstraintKind.SUBTYPE:
                out[mapper(model.object_types[c.subtype_id].name)] = mapper(model.object_types[c.supertype_id].name)
        return out
    bsub = subtype_map(before, lambda n: obj_map.get(n, n))
    asub = subtype_map(after, lambda n: n)
    for sub in sorted(set(bsub) | set(asub)):
        bs, ass = bsub.get(sub), asub.get(sub)
        if bs == ass:
            continue
        if bs is None:
            changes.append(_change(ChangeKind.ADD_SUBTYPE, sub, ChangeSafety.MANUAL, f"{sub} becomes a subtype of {ass}", None, ass, preconditions=("existing subtype identity/population must be aligned with the supertype",)))
        elif ass is None:
            changes.append(_change(ChangeKind.DROP_SUBTYPE, sub, ChangeSafety.MANUAL, f"{sub} is no longer a subtype of {bs}", bs, None, preconditions=("define a new independent identity strategy before detaching the subtype",)))
        else:
            changes.append(_change(ChangeKind.CHANGE_SUBTYPE, sub, ChangeSafety.MANUAL, f"{sub} changes supertype", bs, ass, preconditions=("identity and population inclusion must be migrated explicitly",)))

    # Remaining fact constraints. Map before names/roles into the after namespace so true renames compare equal.
    def fact_mapper(name: str) -> str:
        return fact_map.get(name, name)
    def role_mapper(fact_name: str, role_name: str) -> str:
        return role_maps.get(fact_name, {}).get(role_name, role_name)
    bc = _constraint_records(before, object_mapper=lambda n: obj_map.get(n, n), fact_mapper=fact_mapper, role_mapper=role_mapper)
    ac = _constraint_records(after)
    for key in sorted(set(bc) | set(ac), key=str):
        b, a = bc.get(key), ac.get(key)
        if b == a:
            continue
        if b is None:
            safety, pre = _constraint_add_safety(a)
            changes.append(_change(ChangeKind.ADD_CONSTRAINT, ":".join(map(str, key)), safety, f"add {a['kind']} constraint", None, a, preconditions=pre))
        elif a is None:
            changes.append(_change(ChangeKind.DROP_CONSTRAINT, ":".join(map(str, key)), ChangeSafety.SAFE, f"remove {b['kind']} constraint", b, None))
        elif b["kind"] == "frequency" and a["kind"] == "frequency":
            safety, pre = _frequency_change_safety(b, a)
            changes.append(_change(ChangeKind.CHANGE_CONSTRAINT, ":".join(map(str, key)), safety, "change frequency bounds", b, a, preconditions=pre))
        else:
            changes.append(_change(ChangeKind.CHANGE_CONSTRAINT, ":".join(map(str, key)), ChangeSafety.REQUIRES_DATA_CHECK, f"change {a['kind']} constraint", b, a, preconditions=("existing population must satisfy the changed constraint",)))

    # Readings are semantic documentation and never require physical data changes.
    bread = {before.fact_types[r.fact_type_id].name: r.template for r in before.readings.values() if r.fact_type_id in before.fact_types and r.fact_type_id not in before.field_hints}
    aread = {after.fact_types[r.fact_type_id].name: r.template for r in after.readings.values() if r.fact_type_id in after.fact_types and r.fact_type_id not in after.field_hints}
    bread_mapped = {fact_map.get(k, k): v for k, v in bread.items()}
    for fact in sorted(set(bread_mapped) | set(aread)):
        b, a = bread_mapped.get(fact), aread.get(fact)
        if b == a:
            continue
        if b is None:
            changes.append(_change(ChangeKind.ADD_READING, fact, ChangeSafety.SAFE, f"add canonical reading for {fact}", None, a))
        elif a is None:
            changes.append(_change(ChangeKind.DROP_READING, fact, ChangeSafety.SAFE, f"remove canonical reading for {fact}", b, None))
        else:
            changes.append(_change(ChangeKind.CHANGE_READING, fact, ChangeSafety.SAFE, f"change canonical reading for {fact}", b, a))

    # Warn only when legacy/name-derived identities leave a rename ambiguous.
    dropped_by_kind = {}
    for name in dropped_objects:
        dropped_by_kind.setdefault(_obj_kind(bobjects[name]), []).append(name)
    added_by_kind = {}
    for name in added_objects:
        added_by_kind.setdefault(_obj_kind(aobjects[name]), []).append(name)
    for kind in sorted(set(dropped_by_kind) & set(added_by_kind)):
        warnings.append(
            f"both dropped and added {kind} types exist; if any are renames, add stable identity annotations or provide an object_types migration hint"
        )
    if dropped_facts and added_facts:
        warnings.append("both dropped and added facts exist; if any are renames, add stable identity annotations or provide a facts migration hint")

    changes.sort(key=lambda c: (c.safety.value, c.kind.value, c.subject, c.id))
    return SemanticDiff(before.name, after.name, changes, hints, warnings)
