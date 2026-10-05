from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import Constraint, ConstraintKind, EntityType, Model, ValueType
from .population import SemanticPopulation, validate_population
from .witness import _Builder, _repair_non_target_context


@dataclass(frozen=True)
class AcceptanceProbe:
    """A small source-valid population used to detect target strengthening.

    Counterexamples ask whether an invalid source state survives a target.
    Acceptance probes ask the dual question for selected *valid source states*.
    Rejection of a source-valid probe is evidence that the target is stronger or
    otherwise incompatible with the source semantics for that tested case.

    Passing every generated probe is deliberately not advertised as a proof of
    semantic equivalence: the probe family is finite and obligation-specific.
    """

    obligation_id: str
    kind: str
    probe_id: str
    label: str
    population: SemanticPopulation
    note: str

    def to_dict(self, model: Model) -> dict[str, Any]:
        violations = validate_population(model, self.population)
        return {
            "format": "factgraph-semantic-acceptance-probe-v2",
            "obligation_id": self.obligation_id,
            "kind": self.kind,
            "probe_id": self.probe_id,
            "label": self.label,
            "source_valid": not violations,
            "source_violations": [v.to_dict() for v in violations],
            "population": self.population.to_dict(model),
            "note": self.note,
        }


def _candidate_occurrences(model: Model, value_type_id: str) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    for fact in model.fact_types.values():
        for role in fact.roles:
            if role.player_id != value_type_id:
                continue
            priority = 2
            hint = model.field_hints.get(fact.id)
            if hint is not None:
                priority = 0
            elif fact.id not in model.field_hints:
                priority = 1
            rows.append((priority, fact.id, role.id))
    return sorted(rows, key=lambda x: (x[0], x[1], x[2]))


def _remove_unused_value(pop: SemanticPopulation, iid: str) -> None:
    if any(iid in row for rows in pop.facts.values() for row in rows):
        return
    vt = pop.value_types.pop(iid, None)
    pop.values.pop(iid, None)
    if vt is not None:
        members = pop.memberships.get(vt)
        if members is not None:
            members.discard(iid)
            if not members:
                pop.memberships.pop(vt, None)


def _value_probe_population(model: Model, value_type: ValueType, literal: Any, obligation_id: str) -> SemanticPopulation | None:
    """Embed a valid boundary literal in the smallest deterministic source context."""
    for _priority, fact_id, role_id in _candidate_occurrences(model, value_type.id):
        fact = model.fact_types[fact_id]
        role = next(r for r in fact.roles if r.id == role_id)
        b = _Builder(model)
        try:
            row = b.fact_row(fact_id, 0)
        except Exception:
            continue
        old_iid = row[role.ordinal]
        new_iid = b.instance(value_type.id, 777, literal=literal)
        replacement = list(row)
        replacement[role.ordinal] = new_iid
        rows = b.pop.facts.get(fact_id, [])
        for idx, existing in enumerate(rows):
            if existing == row:
                rows[idx] = tuple(replacement)
                break
        else:
            continue
        _remove_unused_value(b.pop, old_iid)
        try:
            _repair_non_target_context(model, b, obligation_id)
        except Exception:
            continue
        if not validate_population(model, b.pop):
            return b.pop

    # A standalone value is still a valid conceptual probe, although target
    # lowerers may correctly report that it lacks physical storage context.
    b = _Builder(model)
    b.instance(value_type.id, 777, literal=literal)
    return b.pop if not validate_population(model, b.pop) else None


def _make_probe(
    model: Model,
    obligation_id: str,
    kind: str,
    suffix: str,
    label: str,
    builder: _Builder,
    note: str,
    *,
    repair: bool = True,
) -> AcceptanceProbe | None:
    if repair:
        try:
            _repair_non_target_context(model, builder, obligation_id)
        except Exception:
            return None
    if validate_population(model, builder.pop):
        return None
    return AcceptanceProbe(
        obligation_id=obligation_id,
        kind=kind,
        probe_id=f"{obligation_id}::accept::{suffix}",
        label=label,
        population=builder.pop,
        note=note,
    )


def _value_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.object_type_id:
        return ()
    obj = model.object_types.get(c.object_type_id)
    if not isinstance(obj, ValueType):
        return ()
    spec = c.value_spec or {}
    candidates: list[tuple[str, str, Any]] = []
    if spec.get("kind") == "range":
        lo, hi = spec.get("min"), spec.get("max")
        candidates.append(("lower_boundary", f"source lower boundary {lo!r}", lo))
        if hi != lo:
            candidates.append(("upper_boundary", f"source upper boundary {hi!r}", hi))
    elif spec.get("kind") == "oneof":
        for idx, value in enumerate(list(spec.get("values", []))):
            candidates.append((f"allowed_{idx + 1}", f"declared allowed value {value!r}", value))
    else:
        return ()

    probes: list[AcceptanceProbe] = []
    for suffix, label, literal in candidates:
        pop = _value_probe_population(model, obj, literal, c.id)
        if pop is None:
            continue
        probes.append(AcceptanceProbe(
            obligation_id=c.id,
            kind=kind,
            probe_id=f"{c.id}::accept::{suffix}",
            label=label,
            population=pop,
            note=(
                "This population is valid under the source semantic model and sits on a declared value-domain boundary. "
                "If a target rejects it while also rejecting the invalid counterexample, the target is stronger or otherwise incompatible for this tested case."
            ),
        ))
    return tuple(probes)


def _fact_set_probe(model: Model, obligation_id: str, kind: str) -> tuple[AcceptanceProbe, ...]:
    prefix = "obligation:set:"
    if not obligation_id.startswith(prefix):
        return ()
    fact_id = obligation_id[len(prefix):]
    if fact_id not in model.fact_types:
        return ()
    b = _Builder(model)
    b.fact_row(fact_id, 0)
    probe = _make_probe(
        model, obligation_id, kind, "single_fact", "one source-valid fact occurrence", b,
        "One occurrence of the fact is source-valid. Rejection would show that the target is stricter or physically incompatible even before duplicate/set semantics are challenged.",
    )
    return (probe,) if probe else ()


def _mandatory_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.fact_type_id or not c.role_ids:
        return ()
    fact = model.fact_types[c.fact_type_id]
    role = next((r for r in fact.roles if r.id == c.role_ids[0]), None)
    if role is None:
        return ()
    b = _Builder(model)
    row = b.fact_row(fact.id, 0)
    participant = row[role.ordinal]
    probe = _make_probe(
        model, c.id, kind, "participating_instance", f"source-valid participation in {fact.name}.{role.name}", b,
        f"The source-valid population includes {participant} and a {fact.name} fact in which it plays mandatory role {role.name}. Rejection is strengthening/incompatibility evidence for this tested participation case.",
    )
    return (probe,) if probe else ()


def _uniqueness_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.fact_type_id:
        return ()
    fact = model.fact_types[c.fact_type_id]
    b = _Builder(model)
    first = b.fact_row(fact.id, 0)

    # The strongest useful positive case is two facts whose constrained keys are
    # distinct while every other role stays equal. A target that accidentally
    # places uniqueness on a different/free role can reject this source-valid pair.
    second_added = False
    for rid in c.role_ids:
        role = next((r for r in fact.roles if r.id == rid), None)
        if role is None:
            continue
        distinct = b.distinct_instance(role.player_id, first[role.ordinal], start_variant=50)
        if distinct is None:
            continue
        fixed = {r.id: first[r.ordinal] for r in fact.roles}
        fixed[role.id] = distinct
        b.fact_row(fact.id, 1, fixed=fixed)
        second_added = True
        break

    suffix = "distinct_keys" if second_added else "single_valid_tuple"
    label = "two facts with distinct declared unique keys" if second_added else "one source-valid fact under uniqueness"
    probe = _make_probe(
        model, c.id, kind, suffix, label, b,
        "The population satisfies the declared uniqueness rule. Rejection may expose a stronger or differently placed target uniqueness constraint.",
    )
    return (probe,) if probe else ()


def _identifier_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.object_type_id or not isinstance(model.object_types.get(c.object_type_id), EntityType):
        return ()
    b = _Builder(model)
    b.instance(c.object_type_id, 0)
    b.instance(c.object_type_id, 1)
    probe = _make_probe(
        model, c.id, kind, "distinct_identifiers", "two entities with distinct preferred identifiers", b,
        "Two distinct entity instances with distinct source-valid preferred identifiers must remain representable. Rejection is evidence of a stronger/incompatible target identification rule.",
    )
    return (probe,) if probe else ()


def _frequency_population(model: Model, c: Constraint, count: int) -> _Builder | None:
    if not c.fact_type_id or count <= 0:
        return None
    fact = model.fact_types[c.fact_type_id]
    constrained = set(c.role_ids)
    free = [r for r in fact.roles if r.id not in constrained]
    b = _Builder(model)
    first = b.fact_row(fact.id, 0)
    if count == 1:
        return b
    if not free:
        return None
    fixed_base = {r.id: first[r.ordinal] for r in fact.roles if r.id in constrained}
    used_rows = {first}
    for idx in range(1, count):
        added = False
        # Vary one free role deterministically; preserve the constrained key.
        for free_role in free:
            for variant in range(100 + idx * 17, 100 + idx * 17 + 64):
                fixed = dict(fixed_base)
                fixed[free_role.id] = b.instance(free_role.player_id, variant)
                row = tuple(fixed.get(r.id, first[r.ordinal]) for r in fact.roles)
                if row in used_rows:
                    continue
                b.pop.add_fact(fact.id, row)
                used_rows.add(row)
                added = True
                break
            if added:
                break
        if not added:
            return None
    return b


def _frequency_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    lo = c.min_frequency or 0
    hi = c.max_frequency
    counts: list[tuple[str, int]] = []
    if lo > 0:
        counts.append(("minimum", lo))
    if hi is not None and hi > 0 and hi != lo:
        counts.append(("maximum", hi))
    if not counts and hi is not None and hi > 0:
        counts.append(("maximum", hi))
    probes: list[AcceptanceProbe] = []
    for label, count in counts:
        b = _frequency_population(model, c, count)
        if b is None:
            continue
        probe = _make_probe(
            model, c.id, kind, f"{label}_{count}", f"source-valid {label} frequency {count}", b,
            f"The population realizes exactly the declared {label} frequency {count} for one observed key. Rejection is evidence of a stronger/incompatible target cardinality rule.",
        )
        if probe:
            probes.append(probe)
    return tuple(probes)


def _subtype_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.subtype_id or not c.supertype_id:
        return ()
    b = _Builder(model)
    iid = b.instance(c.subtype_id, 0, include_supertypes=True)
    probe = _make_probe(
        model, c.id, kind, "included_instance", "subtype instance included in its supertype", b,
        f"Instance {iid} is source-valid as both subtype and supertype. Rejection is evidence that the target's subtype representation is stronger or incompatible for this case.",
    )
    return (probe,) if probe else ()


def _symmetric_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if c.ring_kind != "symmetric" or not c.fact_type_id:
        return ()
    fact = model.fact_types[c.fact_type_id]
    if len(fact.roles) != 2:
        return ()
    b = _Builder(model)
    row = b.fact_row(fact.id, 0, role_variants={fact.roles[0].id: 0, fact.roles[1].id: 1})
    b.pop.add_fact(fact.id, (row[1], row[0]))
    probe = _make_probe(
        model, c.id, kind, "symmetric_pair", "both directions of a symmetric fact", b,
        "The source-valid population contains both directions required by logical symmetry. Rejection is strengthening/incompatibility evidence for this tested symmetric pair.",
    )
    return (probe,) if probe else ()


def _unordered_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.fact_type_id:
        return ()
    b = _Builder(model)
    b.fact_row(c.fact_type_id, 0)
    probe = _make_probe(
        model, c.id, kind, "one_representative", "one representative of an unordered fact", b,
        "A single representative of the unordered fact is source-valid. Rejection is evidence that the target is stricter/incompatible for this tested occurrence.",
    )
    return (probe,) if probe else ()


def _set_relation_probes(model: Model, c: Constraint, kind: str) -> tuple[AcceptanceProbe, ...]:
    if not c.fact_type_id or not c.target_fact_type_id:
        return ()
    left = model.fact_types[c.fact_type_id]
    right = model.fact_types[c.target_fact_type_id]
    b = _Builder(model)
    left_row = b.fact_row(left.id, 0)
    fixed_right: dict[str, str] = {}
    for lrid, rrid in zip(c.role_ids, c.target_role_ids):
        lpos = next(i for i, r in enumerate(left.roles) if r.id == lrid)
        fixed_right[rrid] = left_row[lpos]

    if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY}:
        b.fact_row(right.id, 0, fixed=fixed_right)
        suffix = "matching_projections"
        label = "matching left/right projected tuple"
        note = "The projected tuple appears on both sides, satisfying the source subset/equality relation. Rejection is stronger/incompatible evidence for this tested matching tuple."
    elif c.kind == ConstraintKind.EXCLUSION:
        # Keep both fact types populated while making at least one projected value
        # different, so the projection sets are disjoint as required.
        changed = False
        for rrid in c.target_role_ids:
            role = next((r for r in right.roles if r.id == rrid), None)
            if role is None:
                continue
            current = fixed_right.get(rrid)
            if current is None:
                continue
            distinct = b.distinct_instance(role.player_id, current, start_variant=90)
            if distinct is not None:
                fixed_right[rrid] = distinct
                changed = True
                break
        if not changed:
            # Left-only is still a source-valid exclusion population and is more
            # useful than making a false distinctness claim over a singleton domain.
            suffix = "left_only"
            label = "one projected tuple on only one exclusion side"
            note = "Only one side of the exclusion pair is populated, which is source-valid. Rejection is stronger/incompatible evidence for this tested occurrence."
        else:
            b.fact_row(right.id, 1, fixed=fixed_right)
            suffix = "disjoint_projections"
            label = "disjoint left/right projected tuples"
            note = "Both sides are populated with disjoint projections, satisfying source exclusion. Rejection is stronger/incompatible evidence for this tested pair."
    else:
        return ()

    probe = _make_probe(model, c.id, kind, suffix, label, b, note)
    return (probe,) if probe else ()


def synthesize_acceptance_probes(model: Model, obligation_id: str, kind: str) -> tuple[AcceptanceProbe, ...]:
    """Generate small source-valid probes for selected semantic obligations.

    v2 extends the original value-boundary probes to relationship and identity
    obligations. Each probe must validate against the entire source model before
    it is returned. The family is intentionally finite and conservative: absence
    of a probe means "not yet specified", not that the source rule lacks valid
    populations.
    """
    if kind == "fact_set_semantics":
        return _fact_set_probe(model, obligation_id, kind)

    c = model.constraints.get(obligation_id)
    if c is None:
        return ()
    if c.kind == ConstraintKind.VALUE:
        return _value_probes(model, c, kind)
    if c.kind == ConstraintKind.MANDATORY:
        return _mandatory_probes(model, c, kind)
    if c.kind == ConstraintKind.UNIQUENESS:
        return _uniqueness_probes(model, c, kind)
    if c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
        return _identifier_probes(model, c, kind)
    if c.kind == ConstraintKind.FREQUENCY:
        return _frequency_probes(model, c, kind)
    if c.kind == ConstraintKind.SUBTYPE:
        return _subtype_probes(model, c, kind)
    if c.kind == ConstraintKind.RING:
        return _symmetric_probes(model, c, kind)
    if c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
        return _unordered_probes(model, c, kind)
    if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
        return _set_relation_probes(model, c, kind)
    return ()
