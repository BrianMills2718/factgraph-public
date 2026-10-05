from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .model import ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType


def _json_literal(value: Any) -> Any:
    """Convert semantic scalar literals to deterministic JSON values."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        # Preserve lexical precision rather than passing through binary float.
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


@dataclass
class SemanticPopulation:
    """Small target-independent population used as a semantic test oracle.

    Object-type populations are explicit sets of instance ids.  Value instances
    additionally carry their literal.  Fact rows contain instance ids in the
    normalized role order.  This is intentionally independent of any physical
    database representation.
    """

    memberships: dict[str, set[str]] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    value_types: dict[str, str] = field(default_factory=dict)
    facts: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    # v2 extension: objectified instance -> (objectified object type, source fact, row occurrence index).
    # The row index is occurrence-sensitive so even an intentionally duplicated
    # invalid fact population can be lowered without guessing which relation
    # occurrence an objectified player denotes. Empty mappings retain the v1
    # serialization contract for backward compatibility.
    objectifications: dict[str, tuple[str, str, int]] = field(default_factory=dict)

    def add_membership(self, object_type_id: str, instance_id: str) -> None:
        self.memberships.setdefault(object_type_id, set()).add(instance_id)

    def add_value(self, value_type_id: str, instance_id: str, literal: Any) -> None:
        self.add_membership(value_type_id, instance_id)
        self.values[instance_id] = literal
        self.value_types[instance_id] = value_type_id

    def add_fact(self, fact_type_id: str, row: tuple[str, ...]) -> None:
        self.facts.setdefault(fact_type_id, []).append(tuple(row))

    def bind_objectification(
        self, object_type_id: str, instance_id: str, fact_type_id: str, row_index: int
    ) -> None:
        self.add_membership(object_type_id, instance_id)
        self.objectifications[instance_id] = (object_type_id, fact_type_id, int(row_index))

    def to_dict(self, model: Model | None = None) -> dict[str, Any]:
        def obj_name(oid: str) -> str:
            return model.object_types[oid].name if model and oid in model.object_types else oid

        def fact_name(fid: str) -> str:
            return model.fact_types[fid].name if model and fid in model.fact_types else fid

        payload = {
            "format": "factgraph-semantic-population-v2" if self.objectifications else "factgraph-semantic-population-v1",
            "memberships": [
                {"object_type_id": oid, "object_type": obj_name(oid), "instances": sorted(ids)}
                for oid, ids in sorted(self.memberships.items())
            ],
            "values": [
                {
                    "instance_id": iid,
                    "value_type_id": self.value_types[iid],
                    "value_type": obj_name(self.value_types[iid]),
                    "literal": _json_literal(self.values[iid]),
                }
                for iid in sorted(self.values)
            ],
            "facts": [
                {
                    "fact_type_id": fid,
                    "fact_type": fact_name(fid),
                    "rows": [list(row) for row in rows],
                }
                for fid, rows in sorted(self.facts.items())
            ],
        }
        if self.objectifications:
            payload["objectifications"] = [
                {
                    "instance_id": iid,
                    "object_type_id": oid,
                    "object_type": obj_name(oid),
                    "fact_type_id": fid,
                    "fact_type": fact_name(fid),
                    "row_index": row_index,
                }
                for iid, (oid, fid, row_index) in sorted(self.objectifications.items())
            ]
        return payload


@dataclass(frozen=True)
class PopulationViolation:
    obligation_id: str
    code: str
    message: str
    source_element_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _literal_allowed(model: Model, value_type: ValueType, literal: Any) -> bool:
    for c in model.constraints_for_value(value_type.id):
        spec = c.value_spec or {}
        kind = spec.get("kind")
        if kind == "oneof":
            allowed = spec.get("values", [])
            if literal not in allowed and str(literal) not in {str(v) for v in allowed}:
                return False
        elif kind == "range":
            lo, hi = spec.get("min"), spec.get("max")
            try:
                if value_type.scalar_kind == "Int":
                    value, lo_v, hi_v = int(literal), int(lo), int(hi)
                elif value_type.scalar_kind in {"Float", "Decimal"}:
                    value, lo_v, hi_v = Decimal(str(literal)), Decimal(str(lo)), Decimal(str(hi))
                elif value_type.scalar_kind == "Date":
                    value, lo_v, hi_v = date.fromisoformat(str(literal)), date.fromisoformat(str(lo)), date.fromisoformat(str(hi))
                elif value_type.scalar_kind == "Timestamp":
                    value, lo_v, hi_v = datetime.fromisoformat(str(literal)), datetime.fromisoformat(str(lo)), datetime.fromisoformat(str(hi))
                else:
                    value, lo_v, hi_v = str(literal), str(lo), str(hi)
                if value < lo_v or value > hi_v:
                    return False
            except (TypeError, ValueError, ArithmeticError):
                return False
    return True


def _project(model: Model, pop: SemanticPopulation, fact_id: str, role_ids: tuple[str, ...]) -> set[tuple[str, ...]]:
    fact = model.fact_types[fact_id]
    positions = [next(i for i, role in enumerate(fact.roles) if role.id == rid) for rid in role_ids]
    return {tuple(row[p] for p in positions) for row in pop.facts.get(fact_id, [])}


def validate_population(model: Model, pop: SemanticPopulation) -> list[PopulationViolation]:
    out: list[PopulationViolation] = []

    # Population representation validity and role typing.
    for oid, instances in sorted(pop.memberships.items()):
        if oid not in model.object_types:
            out.append(PopulationViolation(f"population:membership:{oid}", "UNKNOWN_OBJECT_TYPE", f"population references unknown object type {oid}", oid))
        for iid in instances:
            if iid in pop.value_types and pop.value_types[iid] != oid and isinstance(model.object_types.get(oid), ValueType):
                out.append(PopulationViolation(f"population:value:{iid}", "VALUE_TYPE_MISMATCH", f"value instance {iid} is assigned to inconsistent value types", oid))

    for fid, rows in sorted(pop.facts.items()):
        fact = model.fact_types.get(fid)
        if fact is None:
            out.append(PopulationViolation(f"population:fact:{fid}", "UNKNOWN_FACT_TYPE", f"population references unknown fact type {fid}", fid))
            continue
        for row in rows:
            if len(row) != len(fact.roles):
                out.append(PopulationViolation(f"population:fact:{fid}", "FACT_ARITY", f"fact {fact.name} row has arity {len(row)} not {len(fact.roles)}", fid))
                continue
            for role, iid in zip(fact.roles, row):
                if iid not in pop.memberships.get(role.player_id, set()):
                    out.append(PopulationViolation(f"population:role:{role.id}", "ROLE_PLAYER_MEMBERSHIP", f"instance {iid} is not in the population of {model.object_types[role.player_id].name} for role {role.name}", role.id))

    # Optional v2 objectification occurrence bindings. Bindings are validated
    # when present, but v1 populations remain legal and do not acquire an
    # implicit "every objectified membership must be bound" requirement.
    occurrence_owners: dict[tuple[str, int], str] = {}
    for iid, (oid, fid, row_index) in sorted(pop.objectifications.items()):
        obj = model.object_types.get(oid)
        if not isinstance(obj, ObjectifiedFactType):
            out.append(PopulationViolation(f"population:objectification:{iid}", "OBJECTIFICATION_TYPE", f"binding {iid} does not name an ObjectifiedFactType", oid))
            continue
        if iid not in pop.memberships.get(oid, set()):
            out.append(PopulationViolation(f"population:objectification:{iid}", "OBJECTIFICATION_MEMBERSHIP", f"objectified instance {iid} is not a member of {obj.name}", oid))
        if fid != obj.fact_type_id:
            out.append(PopulationViolation(f"population:objectification:{iid}", "OBJECTIFICATION_FACT", f"objectified instance {iid} binds {fid}, but {obj.name} objectifies {obj.fact_type_id}", oid))
            continue
        rows = pop.facts.get(fid, [])
        if row_index < 0 or row_index >= len(rows):
            out.append(PopulationViolation(f"population:objectification:{iid}", "OBJECTIFICATION_OCCURRENCE", f"objectified instance {iid} references missing row occurrence {row_index} of {fid}", fid))
            continue
        occurrence = (fid, row_index)
        other = occurrence_owners.get(occurrence)
        if other is not None and other != iid:
            out.append(PopulationViolation(f"population:objectification:{iid}", "OBJECTIFICATION_INJECTIVITY", f"fact occurrence {fid}[{row_index}] is bound to both {other} and {iid}", fid))
        else:
            occurrence_owners[occurrence] = iid

    # Value-type constraints apply to explicit value populations, even when a
    # value is not currently used by a fact.  This makes minimal counterexamples
    # possible without inventing an unrelated relationship.
    for oid, obj in sorted(model.object_types.items()):
        if not isinstance(obj, ValueType):
            continue
        for iid in sorted(pop.memberships.get(oid, set())):
            if iid not in pop.values:
                out.append(PopulationViolation(f"population:value:{iid}", "MISSING_VALUE_LITERAL", f"value instance {iid} has no literal", oid))
                continue
            if not _literal_allowed(model, obj, pop.values[iid]):
                for c in model.constraints_for_value(obj.id):
                    spec = c.value_spec or {}
                    # Emit only the constraints actually violated when possible.
                    tmp = SemanticPopulation()
                    tmp.add_value(obj.id, iid, pop.values[iid])
                    # local check without recursion: evaluate the single spec
                    allowed = True
                    if spec.get("kind") == "oneof":
                        vals = spec.get("values", [])
                        allowed = pop.values[iid] in vals or str(pop.values[iid]) in {str(v) for v in vals}
                    elif spec.get("kind") == "range":
                        shadow = Model(model.id, model.name)
                        shadow.object_types[obj.id] = obj
                        shadow.constraints[c.id] = c
                        allowed = _literal_allowed(shadow, obj, pop.values[iid])
                    if not allowed:
                        out.append(PopulationViolation(c.id, "VALUE_CONSTRAINT_VIOLATION", f"value {pop.values[iid]!r} violates {obj.name} {spec}", c.id))

    # Fact set semantics and local constraints.
    for fid, fact in sorted(model.fact_types.items()):
        rows = list(pop.facts.get(fid, []))
        counts = Counter(rows)
        if any(n > 1 for n in counts.values()):
            out.append(PopulationViolation(f"obligation:set:{fid}", "DUPLICATE_FACT", f"{fact.name} contains an identical tuple more than once", fid))

        # The conceptual fact population is a set.  A repeated physical row is
        # diagnosed above as a set-semantics violation, but it must not be
        # counted twice when evaluating other conceptual constraints.  Without
        # this distinction a duplicate tuple could spuriously violate a
        # frequency bound even though there is still only one semantic fact.
        semantic_rows = list(dict.fromkeys(rows))

        for c in model.constraints_for_fact(fid):
            positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in c.role_ids]
            if c.kind == ConstraintKind.UNIQUENESS:
                groups: dict[tuple[str, ...], set[tuple[str, ...]]] = defaultdict(set)
                for row in semantic_rows:
                    groups[tuple(row[p] for p in positions)].add(row)
                bad = [key for key, distinct in groups.items() if len(distinct) > 1]
                if bad:
                    out.append(PopulationViolation(c.id, "UNIQUENESS_VIOLATION", f"{fact.name} uniqueness is violated for projected key {bad[0]}", c.id))
            elif c.kind == ConstraintKind.MANDATORY and c.role_ids:
                role = next(r for r in fact.roles if r.id == c.role_ids[0])
                pos = next(i for i, r in enumerate(fact.roles) if r.id == role.id)
                participating = {row[pos] for row in semantic_rows}
                missing = sorted(pop.memberships.get(role.player_id, set()) - participating)
                if missing:
                    out.append(PopulationViolation(c.id, "MANDATORY_VIOLATION", f"{model.object_types[role.player_id].name} instance {missing[0]} does not participate in {fact.name}.{role.name}", c.id))
            elif c.kind == ConstraintKind.FREQUENCY:
                groups = Counter(tuple(row[p] for p in positions) for row in semantic_rows)
                for key, count in groups.items():
                    lo = c.min_frequency or 0
                    hi = c.max_frequency if c.max_frequency is not None else count
                    if count < lo or count > hi:
                        out.append(PopulationViolation(c.id, "FREQUENCY_VIOLATION", f"{fact.name} projected key {key} occurs {count} times, outside {lo}..{c.max_frequency}", c.id))
                        break
            elif c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
                canonical: dict[tuple[str, ...], tuple[str, ...]] = {}
                group_positions = positions
                for row in semantic_rows:
                    mutable = list(row)
                    ordered = sorted(mutable[p] for p in group_positions)
                    for p, value in zip(sorted(group_positions), ordered):
                        mutable[p] = value
                    key = tuple(mutable)
                    prior = canonical.get(key)
                    if prior is not None and prior != row:
                        out.append(PopulationViolation(c.id, "UNORDERED_DUPLICATE", f"{fact.name} contains distinct role permutations of one unordered fact", c.id))
                        break
                    canonical[key] = row
            elif c.kind == ConstraintKind.RING and c.ring_kind == "symmetric" and len(fact.roles) == 2:
                rowset = set(semantic_rows)
                missing_reverse = next((row for row in rowset if (row[1], row[0]) not in rowset), None)
                if missing_reverse is not None:
                    out.append(PopulationViolation(c.id, "SYMMETRY_VIOLATION", f"{fact.name}{missing_reverse} has no reverse tuple", c.id))

    # Preferred identification uses the field-origin fact population.  Each
    # object must have one value for every identifier component, and the tuple of
    # those values must be unique across distinct objects.
    for c in sorted(model.constraints.values(), key=lambda x: x.id):
        if c.kind != ConstraintKind.PREFERRED_IDENTIFIER or c.object_type_id is None:
            continue
        object_ids = sorted(pop.memberships.get(c.object_type_id, set()))
        seen: dict[tuple[str, ...], str] = {}
        for iid in object_ids:
            components: list[str] = []
            complete = True
            for ffid in c.field_fact_ids:
                fact = model.fact_types[ffid]
                owner_pos = next(i for i, r in enumerate(fact.roles) if r.name == "owner")
                value_pos = next(i for i, r in enumerate(fact.roles) if r.name == "value")
                vals = [row[value_pos] for row in pop.facts.get(ffid, []) if row[owner_pos] == iid]
                if len(vals) != 1:
                    # Existence/single-valuedness belongs to the field's mandatory
                    # and uniqueness obligations.  Preferred identification only
                    # compares complete identifier tuples; duplicating those rules
                    # here would make independent counterexamples impossible.
                    complete = False
                    break
                components.append(vals[0])
            if not complete:
                continue
            key = tuple(components)
            if key in seen and seen[key] != iid:
                out.append(PopulationViolation(c.id, "IDENTIFIER_VIOLATION", f"distinct {model.object_types[c.object_type_id].name} instances share preferred identifier {key}", c.id))
                break
            seen[key] = iid

    # Set comparisons.
    for c in sorted(model.constraints.values(), key=lambda x: x.id):
        if c.kind not in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
            continue
        left = _project(model, pop, c.fact_type_id, c.role_ids)
        right = _project(model, pop, c.target_fact_type_id, c.target_role_ids)
        if c.kind == ConstraintKind.SUBSET and not left.issubset(right):
            out.append(PopulationViolation(c.id, "SUBSET_VIOLATION", f"left projection contains values absent from target: {sorted(left-right)[:1]}", c.id))
        elif c.kind == ConstraintKind.EQUALITY and left != right:
            out.append(PopulationViolation(c.id, "EQUALITY_VIOLATION", f"projection sets differ: left-only={sorted(left-right)[:1]} right-only={sorted(right-left)[:1]}", c.id))
        elif c.kind == ConstraintKind.EXCLUSION and left.intersection(right):
            out.append(PopulationViolation(c.id, "EXCLUSION_VIOLATION", f"projection sets overlap: {sorted(left.intersection(right))[:1]}", c.id))

    # Subtype population inclusion.
    for c in sorted(model.constraints.values(), key=lambda x: x.id):
        if c.kind != ConstraintKind.SUBTYPE or c.subtype_id is None or c.supertype_id is None:
            continue
        missing = sorted(pop.memberships.get(c.subtype_id, set()) - pop.memberships.get(c.supertype_id, set()))
        if missing:
            out.append(PopulationViolation(c.id, "SUBTYPE_VIOLATION", f"{model.object_types[c.subtype_id].name} instance {missing[0]} is absent from supertype {model.object_types[c.supertype_id].name}", c.id))

    return sorted(out, key=lambda v: (v.obligation_id, v.code, v.message))
