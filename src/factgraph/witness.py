from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .model import ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType
from .population import PopulationViolation, SemanticPopulation, validate_population


@dataclass(frozen=True)
class CounterexampleResult:
    obligation_id: str
    status: str  # isolated | collateral | not_independently_falsifiable | unsupported
    population: SemanticPopulation
    violations: tuple[PopulationViolation, ...]
    target_violation_observed: bool
    note: str
    shrink_steps: int = 0
    shrink_attempts: int = 0
    locally_irreducible: bool = False

    def to_dict(self, model: Model) -> dict[str, Any]:
        return {
            "format": "factgraph-semantic-counterexample-v1",
            "obligation_id": self.obligation_id,
            "status": self.status,
            "target_violation_observed": self.target_violation_observed,
            "note": self.note,
            "minimality": {
                "membership_count": sum(len(v) for v in self.population.memberships.values()),
                "value_count": len(self.population.values),
                "fact_row_count": sum(len(v) for v in self.population.facts.values()),
                "distinct_violation_count": len(self.violations),
                "method": "greedy-single-element-deletion-v1" if self.locally_irreducible else "not_shrunk",
                "shrink_steps": self.shrink_steps,
                "shrink_attempts": self.shrink_attempts,
                "locally_irreducible": self.locally_irreducible,
                "claim": "No single fact-row occurrence or typed population member can be deleted while preserving an isolated violation of the target obligation." if self.locally_irreducible else "No minimality claim is made for this result.",
            },
            "population": self.population.to_dict(model),
            "violations": [v.to_dict() for v in self.violations],
            "oracle": "factgraph-semantic-population-v1",
        }


def _valid_literal(model: Model, vt: ValueType, variant: int) -> Any:
    constraints = model.constraints_for_value(vt.id)
    for c in constraints:
        spec = c.value_spec or {}
        if spec.get("kind") == "oneof" and spec.get("values"):
            vals = list(spec["values"])
            return vals[variant % len(vals)]
    for c in constraints:
        spec = c.value_spec or {}
        if spec.get("kind") == "range":
            lo, hi = spec["min"], spec["max"]
            if vt.scalar_kind == "Int":
                return int(lo) + min(variant, max(0, int(hi) - int(lo)))
            if vt.scalar_kind in {"Float", "Decimal"}:
                lo_f, hi_f = float(lo), float(hi)
                return lo_f + (hi_f - lo_f) * ([0.25, 0.5, 0.75][variant % 3])
            if vt.scalar_kind == "Date":
                lo_d, hi_d = date.fromisoformat(str(lo)), date.fromisoformat(str(hi))
                return (lo_d + timedelta(days=min(variant, max(0, (hi_d - lo_d).days)))).isoformat()
            if vt.scalar_kind == "Timestamp":
                return str(lo)
            return str(lo)
    if vt.scalar_kind == "Int":
        return 100 + variant
    if vt.scalar_kind in {"Float", "Decimal"}:
        return 100.25 + variant
    if vt.scalar_kind == "Bool":
        return bool(variant % 2)
    if vt.scalar_kind == "Date":
        return date(2026, 1, 1) + timedelta(days=variant)
    if vt.scalar_kind == "Timestamp":
        return datetime(2026, 1, 1, 0, 0, 0).isoformat() + "Z"
    if vt.scalar_kind == "UUID":
        return f"00000000-0000-0000-0000-{variant + 1:012d}"
    return f"v{variant + 1}"


def _invalid_literal(model: Model, vt: ValueType, constraint_id: str) -> Any:
    c = model.constraints[constraint_id]
    spec = c.value_spec or {}
    if spec.get("kind") == "oneof":
        vals = list(spec.get("values", []))
        if vt.scalar_kind == "Int":
            used = {int(v) for v in vals}
            x = 10_000
            while x in used:
                x += 1
            return x
        if vt.scalar_kind in {"Float", "Decimal"}:
            used = {str(v) for v in vals}
            x = 10000.125
            while str(x) in used:
                x += 1
            return x
        if vt.scalar_kind == "Bool":
            # A boolean enumeration containing both values cannot be violated by a
            # well-typed boolean literal; use an invalid lexical marker so the
            # witness still demonstrates the domain boundary.
            return "__not_boolean__"
        candidate = "__factgraph_invalid__"
        while candidate in {str(v) for v in vals}:
            candidate += "x"
        return candidate
    if spec.get("kind") == "range":
        lo = spec.get("min")
        if vt.scalar_kind == "Int":
            return int(lo) - 1
        if vt.scalar_kind in {"Float", "Decimal"}:
            return float(lo) - 1.0
        if vt.scalar_kind == "Date":
            return (date.fromisoformat(str(lo)) - timedelta(days=1)).isoformat()
        if vt.scalar_kind == "Timestamp":
            return (datetime.fromisoformat(str(lo).replace("Z", "+00:00")) - timedelta(seconds=1)).isoformat()
        return ""
    return "__factgraph_invalid__"


class _Builder:
    def __init__(self, model: Model):
        self.model = model
        self.pop = SemanticPopulation()
        self._instances: dict[tuple[str, int], str] = {}
        self._identifier_done: set[tuple[str, str]] = set()

    def _supertype_chain(self, object_type_id: str) -> list[str]:
        out: list[str] = []
        cur = object_type_id
        seen: set[str] = set()
        while cur not in seen:
            seen.add(cur)
            sup = self.model.supertype_of(cur)
            if not isinstance(sup, EntityType):
                break
            out.append(sup.id)
            cur = sup.id
        return out

    def instance(self, object_type_id: str, variant: int = 0, *, include_supertypes: bool = True, ensure_identifier: bool = True, literal: Any | None = None) -> str:
        obj = self.model.object_types[object_type_id]
        if isinstance(obj, ValueType):
            lit = _valid_literal(self.model, obj, variant) if literal is None else literal
            # Value instances are identified by their literal in a fact-oriented
            # model; equal lexical values should not become artificial identities.
            iid = f"value:{object_type_id}:{repr(lit)}"
            self.pop.add_value(obj.id, iid, lit)
            return iid

        key = (object_type_id, variant)
        if key in self._instances:
            return self._instances[key]
        iid = f"instance:{object_type_id}:{variant + 1}"
        self._instances[key] = iid
        self.pop.add_membership(object_type_id, iid)
        if include_supertypes and isinstance(obj, EntityType):
            for sup_id in self._supertype_chain(object_type_id):
                self.pop.add_membership(sup_id, iid)
        if ensure_identifier:
            # Ensure identification for every entity population this instance is
            # intentionally a member of.  That keeps unrelated identifier rules
            # from polluting most generated counterexamples.
            for oid, instances in list(self.pop.memberships.items()):
                if iid in instances and isinstance(self.model.object_types.get(oid), EntityType):
                    self.ensure_identifier(oid, iid, variant)
        return iid

    def distinct_instance(self, object_type_id: str, avoid: str, *, start_variant: int = 0, attempts: int = 64) -> str | None:
        """Return a valid instance different from ``avoid`` when the domain permits it.

        Value types may have finite domains, so simply choosing a numerically
        different generator variant is not sufficient.  This bounded search
        also lets the synthesizer recognize genuinely singleton domains.
        """
        for variant in range(start_variant, start_variant + attempts):
            iid = self.instance(object_type_id, variant)
            if iid != avoid:
                return iid
        return None

    def ensure_identifier(self, entity_id: str, iid: str, variant: int) -> None:
        key = (entity_id, iid)
        if key in self._identifier_done:
            return
        self._identifier_done.add(key)
        c = next((x for x in self.model.constraints.values() if x.kind == ConstraintKind.PREFERRED_IDENTIFIER and x.object_type_id == entity_id), None)
        if c is None:
            return
        for component_idx, ffid in enumerate(c.field_fact_ids):
            fact = self.model.fact_types[ffid]
            owner_role = next(r for r in fact.roles if r.name == "owner")
            value_role = next(r for r in fact.roles if r.name == "value")
            value_iid = self.instance(value_role.player_id, variant * 11 + component_idx + 1)
            row = tuple(iid if r.id == owner_role.id else value_iid for r in fact.roles)
            if row not in self.pop.facts.get(ffid, []):
                self.pop.add_fact(ffid, row)

    def fact_row(
        self,
        fact_id: str,
        row_variant: int = 0,
        *,
        fixed: dict[str, str] | None = None,
        role_variants: dict[str, int] | None = None,
        ensure_identifiers: bool = True,
    ) -> tuple[str, ...]:
        fact = self.model.fact_types[fact_id]
        fixed = fixed or {}
        role_variants = role_variants or {}
        values: list[str] = []
        for role in fact.roles:
            if role.id in fixed:
                iid = fixed[role.id]
            else:
                variant = role_variants.get(role.id, row_variant * 17 + role.ordinal)
                iid = self.instance(role.player_id, variant, ensure_identifier=ensure_identifiers)
            values.append(iid)
        row = tuple(values)
        self.pop.add_fact(fact_id, row)
        return row


def _repair_non_target_mandatory(model: Model, b: _Builder, target_obligation_id: str) -> None:
    """Satisfy unrelated total-participation rules when a tiny filler fact can do so.

    This is intentionally bounded rather than a general solver.  It improves
    witness isolation while leaving difficult interactions visible as collateral.
    """
    serial = 700
    for _ in range(12):
        violations = validate_population(model, b.pop)
        pending = [v for v in violations if v.code == "MANDATORY_VIOLATION" and v.obligation_id != target_obligation_id]
        if not pending:
            return
        changed = False
        for violation in pending:
            c = model.constraints.get(violation.obligation_id)
            if c is None or c.fact_type_id is None or not c.role_ids:
                continue
            fact = model.fact_types[c.fact_type_id]
            role = next(r for r in fact.roles if r.id == c.role_ids[0])
            pos = next(i for i, r in enumerate(fact.roles) if r.id == role.id)
            participating = {row[pos] for row in b.pop.facts.get(fact.id, [])}
            missing = sorted(b.pop.memberships.get(role.player_id, set()) - participating)
            for iid in missing:
                b.fact_row(fact.id, serial, fixed={role.id: iid})
                serial += 1
                changed = True
        if not changed:
            return


def _repair_non_target_context(model: Model, b: _Builder, target_obligation_id: str) -> None:
    """Satisfy a bounded subset of surrounding rules without touching the target.

    Used where a counterexample (especially duplicate-fact and exclusion
    witnesses) creates otherwise-valid instances that trigger logically
    independent mandatory, symmetry, subset, or equality obligations.  This is
    deterministic repair, not a general solver.
    """
    serial = 1300
    for _ in range(16):
        before = tuple((fid, tuple(rows)) for fid, rows in sorted(b.pop.facts.items()))
        _repair_non_target_mandatory(model, b, target_obligation_id)

        for c in sorted(model.constraints.values(), key=lambda x: x.id):
            if c.id == target_obligation_id:
                continue
            if c.kind == ConstraintKind.RING and c.ring_kind == "symmetric" and c.fact_type_id:
                fact = model.fact_types[c.fact_type_id]
                if len(fact.roles) == 2:
                    existing = list(dict.fromkeys(b.pop.facts.get(fact.id, [])))
                    rowset = set(existing)
                    for row in existing:
                        rev = (row[1], row[0])
                        if rev not in rowset:
                            b.pop.add_fact(fact.id, rev)
                            rowset.add(rev)

            elif c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY} and c.fact_type_id and c.target_fact_type_id:
                left = model.fact_types[c.fact_type_id]
                right = model.fact_types[c.target_fact_type_id]

                def projection_rows(fact, role_ids):
                    positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in role_ids]
                    return {tuple(row[p] for p in positions) for row in b.pop.facts.get(fact.id, [])}

                left_proj = projection_rows(left, c.role_ids)
                right_proj = projection_rows(right, c.target_role_ids)
                for values in sorted(left_proj - right_proj):
                    fixed = {rid: value for rid, value in zip(c.target_role_ids, values)}
                    b.fact_row(right.id, serial, fixed=fixed)
                    serial += 1
                if c.kind == ConstraintKind.EQUALITY:
                    for values in sorted(right_proj - left_proj):
                        fixed = {rid: value for rid, value in zip(c.role_ids, values)}
                        b.fact_row(left.id, serial, fixed=fixed)
                        serial += 1

        after = tuple((fid, tuple(rows)) for fid, rows in sorted(b.pop.facts.items()))
        if after == before:
            return


def _isolated_target_violation(model: Model, pop: SemanticPopulation, obligation_id: str) -> tuple[bool, tuple[PopulationViolation, ...]]:
    violations = tuple(validate_population(model, pop))
    target = any(v.obligation_id == obligation_id for v in violations)
    isolated = target and all(v.obligation_id == obligation_id for v in violations)
    return isolated, violations


def _delete_membership_atom(model: Model, pop: SemanticPopulation, object_type_id: str, instance_id: str) -> None:
    members = pop.memberships.get(object_type_id)
    if members is not None:
        members.discard(instance_id)
        if not members:
            pop.memberships.pop(object_type_id, None)
    if isinstance(model.object_types.get(object_type_id), ValueType) and pop.value_types.get(instance_id) == object_type_id:
        pop.values.pop(instance_id, None)
        pop.value_types.pop(instance_id, None)


def _shrink_isolated_counterexample(model: Model, pop: SemanticPopulation, obligation_id: str) -> tuple[SemanticPopulation, int, int]:
    """Greedily delete one population atom at a time while isolation survives.

    This proves only local irreducibility under the documented deletion moves;
    it is deliberately not advertised as global cardinality minimality.
    """
    current = deepcopy(pop)
    steps = 0
    attempts = 0
    while True:
        changed = False

        for fid in sorted(current.facts):
            rows = current.facts.get(fid, [])
            for idx in range(len(rows)):
                candidate = deepcopy(current)
                del candidate.facts[fid][idx]
                if not candidate.facts[fid]:
                    candidate.facts.pop(fid, None)
                attempts += 1
                isolated, _ = _isolated_target_violation(model, candidate, obligation_id)
                if isolated:
                    current = candidate
                    steps += 1
                    changed = True
                    break
            if changed:
                break
        if changed:
            continue

        membership_atoms = [
            (oid, iid)
            for oid, ids in sorted(current.memberships.items())
            for iid in sorted(ids)
        ]
        for oid, iid in membership_atoms:
            candidate = deepcopy(current)
            _delete_membership_atom(model, candidate, oid, iid)
            attempts += 1
            isolated, _ = _isolated_target_violation(model, candidate, obligation_id)
            if isolated:
                current = candidate
                steps += 1
                changed = True
                break
        if not changed:
            return current, steps, attempts


def _finish(model: Model, obligation_id: str, pop: SemanticPopulation, *, note: str = "") -> CounterexampleResult:
    violations = tuple(validate_population(model, pop))
    target = any(v.obligation_id == obligation_id for v in violations)
    if not target:
        status = "not_independently_falsifiable" if note else "unsupported"
    else:
        collateral = [v for v in violations if v.obligation_id != obligation_id and not v.obligation_id.startswith("population:")]
        internal = [v for v in violations if v.obligation_id.startswith("population:")]
        status = "isolated" if not collateral and not internal else "collateral"

    shrink_steps = 0
    shrink_attempts = 0
    locally_irreducible = False
    if status == "isolated":
        pop, shrink_steps, shrink_attempts = _shrink_isolated_counterexample(model, pop, obligation_id)
        violations = tuple(validate_population(model, pop))
        target = any(v.obligation_id == obligation_id for v in violations)
        locally_irreducible = target and all(v.obligation_id == obligation_id for v in violations)

    if not note:
        if status == "isolated":
            note = "The source semantic oracle reports exactly the intended obligation as violated; the population was greedily reduced to local irreducibility under single-element deletion."
        elif status == "collateral":
            note = "The intended obligation is violated, but stronger/interacting model rules also reject this small population."
        else:
            note = "The bounded synthesizer could not produce an independent violating population for this obligation."
    return CounterexampleResult(obligation_id, status, pop, violations, target, note, shrink_steps, shrink_attempts, locally_irreducible)


def synthesize_counterexample(model: Model, obligation_id: str, kind: str) -> CounterexampleResult:
    b = _Builder(model)

    if kind == "fact_set_semantics" and obligation_id.startswith("obligation:set:"):
        fid = obligation_id[len("obligation:set:"):]
        row = b.fact_row(fid, 0)
        b.pop.add_fact(fid, row)
        _repair_non_target_context(model, b, obligation_id)
        return _finish(model, obligation_id, b.pop)

    c = model.constraints.get(obligation_id)
    if c is None:
        return CounterexampleResult(obligation_id, "unsupported", b.pop, (), False, "No normalized constraint corresponds to this obligation.")

    if c.kind == ConstraintKind.UNIQUENESS and c.fact_type_id:
        fact = model.fact_types[c.fact_type_id]
        constrained = set(c.role_ids)
        free = [r for r in fact.roles if r.id not in constrained]
        if not free:
            # Full-role uniqueness is implied by the model's intrinsic set
            # semantics, so there is no population that violates this constraint
            # without first violating set semantics.
            return CounterexampleResult(obligation_id, "not_independently_falsifiable", b.pop, (), False, "Uniqueness spans every fact role and is logically implied by intrinsic fact-set semantics.")
        target_is_field_fact = fact.id in model.field_hints
        first = b.fact_row(fact.id, 0, ensure_identifiers=not target_is_field_fact)
        fixed = {r.id: first[r.ordinal] for r in fact.roles if r.id in constrained}
        free_role = free[0]
        first_free = first[free_role.ordinal]
        distinct = b.distinct_instance(free_role.player_id, first_free, start_variant=1)
        if distinct is None:
            return CounterexampleResult(
                obligation_id,
                "not_independently_falsifiable",
                b.pop,
                (),
                False,
                "The unconstrained role has no second valid value in the bounded source domain, so this uniqueness rule is implied by fact-set semantics plus the current value domain.",
            )
        fixed[free_role.id] = distinct
        b.fact_row(fact.id, 1, fixed=fixed, ensure_identifiers=not target_is_field_fact)
        _repair_non_target_context(model, b, obligation_id)
        return _finish(model, obligation_id, b.pop)

    if c.kind == ConstraintKind.MANDATORY and c.fact_type_id and c.role_ids:
        fact = model.fact_types[c.fact_type_id]
        role = next(r for r in fact.roles if r.id == c.role_ids[0])
        target_is_identifier_field = fact.id in model.field_hints and model.field_hints[fact.id].identifier_component
        b.instance(role.player_id, 0, ensure_identifier=not target_is_identifier_field)
        _repair_non_target_context(model, b, obligation_id)
        return _finish(model, obligation_id, b.pop)

    if c.kind == ConstraintKind.FREQUENCY and c.fact_type_id:
        fact = model.fact_types[c.fact_type_id]
        constrained = set(c.role_ids)
        free = [r for r in fact.roles if r.id not in constrained]
        lo = c.min_frequency or 0
        hi = c.max_frequency
        if lo > 1:
            b.fact_row(fact.id, 0)
            _repair_non_target_context(model, b, obligation_id)
            return _finish(model, obligation_id, b.pop)
        if hi is not None and free:
            first = b.fact_row(fact.id, 0)
            fixed = {r.id: first[r.ordinal] for r in fact.roles if r.id in constrained}
            for i in range(1, hi + 1):
                b.fact_row(fact.id, i, fixed=fixed, role_variants={free[0].id: 100 + i})
            _repair_non_target_context(model, b, obligation_id)
            return _finish(model, obligation_id, b.pop)
        return CounterexampleResult(obligation_id, "not_independently_falsifiable", b.pop, (), False, "The requested upper frequency bound cannot be exceeded with distinct set-valued tuples because the constrained roles span the fact, or no bounded violation exists.")

    if c.kind == ConstraintKind.VALUE and c.object_type_id:
        obj = model.object_types[c.object_type_id]
        if not isinstance(obj, ValueType):
            return CounterexampleResult(obligation_id, "unsupported", b.pop, (), False, "Value obligation does not target a ValueType.")
        b.instance(obj.id, 0, literal=_invalid_literal(model, obj, c.id))
        return _finish(model, obligation_id, b.pop)

    if c.kind == ConstraintKind.SUBTYPE and c.subtype_id and c.supertype_id:
        b.instance(c.subtype_id, 0, include_supertypes=False)
        return _finish(model, obligation_id, b.pop)

    if c.kind == ConstraintKind.RING and c.ring_kind == "symmetric" and c.fact_type_id:
        fact = model.fact_types[c.fact_type_id]
        if len(fact.roles) != 2:
            return CounterexampleResult(obligation_id, "unsupported", b.pop, (), False, "Symmetric witness requires a binary fact.")
        b.fact_row(fact.id, 0, role_variants={fact.roles[0].id: 0, fact.roles[1].id: 1})
        _repair_non_target_context(model, b, obligation_id)
        return _finish(model, obligation_id, b.pop)

    if c.kind == ConstraintKind.UNORDERED_ROLE_GROUP and c.fact_type_id:
        fact = model.fact_types[c.fact_type_id]
        positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in c.role_ids]
        if len(positions) < 2:
            return CounterexampleResult(obligation_id, "unsupported", b.pop, (), False, "Unordered witness requires at least two role positions.")
        row = list(b.fact_row(fact.id, 0))
        permuted = list(row)
        permuted[positions[0]], permuted[positions[1]] = permuted[positions[1]], permuted[positions[0]]
        b.pop.add_fact(fact.id, tuple(permuted))
        return _finish(model, obligation_id, b.pop)

    if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION} and c.fact_type_id and c.target_fact_type_id:
        left = model.fact_types[c.fact_type_id]
        right = model.fact_types[c.target_fact_type_id]
        left_row = b.fact_row(left.id, 0)
        fixed_right: dict[str, str] = {}
        for lrid, rrid in zip(c.role_ids, c.target_role_ids):
            lpos = next(i for i, r in enumerate(left.roles) if r.id == lrid)
            fixed_right[rrid] = left_row[lpos]
        if c.kind == ConstraintKind.EXCLUSION:
            b.fact_row(right.id, 0, fixed=fixed_right)
            _repair_non_target_context(model, b, obligation_id)
        elif c.kind == ConstraintKind.EQUALITY:
            # left-only is enough to violate equality.
            pass
        # subset is also left-only.
        return _finish(model, obligation_id, b.pop)

    if c.kind == ConstraintKind.PREFERRED_IDENTIFIER and c.object_type_id:
        obj = model.object_types[c.object_type_id]
        if not isinstance(obj, EntityType):
            return CounterexampleResult(obligation_id, "unsupported", b.pop, (), False, "Preferred identifier witness currently targets entity types.")
        a = b.instance(obj.id, 0, ensure_identifier=False)
        z = b.instance(obj.id, 1, ensure_identifier=False)
        # Make inherited supertype identifiers valid if present; the target
        # object's own identifier is intentionally populated identically below.
        for sup_id in b._supertype_chain(obj.id):
            b.ensure_identifier(sup_id, a, 0)
            b.ensure_identifier(sup_id, z, 1)
        for component_idx, ffid in enumerate(c.field_fact_ids):
            fact = model.fact_types[ffid]
            owner_role = next(r for r in fact.roles if r.name == "owner")
            value_role = next(r for r in fact.roles if r.name == "value")
            shared_value = b.instance(value_role.player_id, 500 + component_idx)
            for owner in (a, z):
                row = tuple(owner if r.id == owner_role.id else shared_value for r in fact.roles)
                b.pop.add_fact(ffid, row)
        _repair_non_target_context(model, b, obligation_id)
        return _finish(model, obligation_id, b.pop)

    return CounterexampleResult(obligation_id, "unsupported", b.pop, (), False, f"No bounded source-level synthesizer is implemented for {c.kind.value}.")
