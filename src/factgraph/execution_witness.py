from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .model import ConstraintKind, EntityType, Model, ObjectifiedFactType, ValueType
from .population import SemanticPopulation, validate_population
from .witness import CounterexampleResult, _Builder, _repair_non_target_context, _valid_literal


@dataclass(frozen=True)
class ExecutionWitness:
    """Source-semantic population prepared for physical execution.

    The minimal semantic counterexample remains the canonical proof object.  Some
    targets do not materialize standalone ValueType populations, so execution may
    require *target-independent* valid context around that same violating value.
    Context is accepted only when the source oracle still reports exactly the
    original obligation as violated.
    """

    population: SemanticPopulation
    mode: str  # exact_minimal_source | contextualized_source_core
    source_obligation_id: str
    context_added: bool
    context_fact_type_id: str | None = None
    context_role_id: str | None = None
    added_membership_count: int = 0
    added_fact_row_count: int = 0
    note: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "format": "factgraph-execution-witness-v1",
            "mode": self.mode,
            "source_obligation_id": self.source_obligation_id,
            "context_added": self.context_added,
            "context_fact_type_id": self.context_fact_type_id,
            "context_role_id": self.context_role_id,
            "added_membership_count": self.added_membership_count,
            "added_fact_row_count": self.added_fact_row_count,
            "note": self.note,
        }


def _atom_counts(pop: SemanticPopulation) -> tuple[int, int]:
    return sum(len(v) for v in pop.memberships.values()), sum(len(v) for v in pop.facts.values())


def _standalone_values(pop: SemanticPopulation) -> list[str]:
    used = {iid for rows in pop.facts.values() for row in rows for iid in row}
    return sorted(iid for iid in pop.values if iid not in used)


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


def _candidate_occurrences(model: Model, value_type_id: str) -> list[tuple[int, str, str]]:
    """Return deterministic (priority, fact id, role id) occurrence candidates."""
    rows: list[tuple[int, str, str]] = []
    for fact in model.fact_types.values():
        for role in fact.roles:
            if role.player_id != value_type_id:
                continue
            priority = 3
            hint = model.field_hints.get(fact.id)
            if hint is not None:
                owner = model.object_types.get(hint.owner_object_type_id)
                # Ordinary entity scalar fields are the most direct storage context.
                priority = 0 if isinstance(owner, EntityType) else 2
            else:
                # Prefer ordinary relationship value roles before objectification-
                # dependent contexts.
                if all(not isinstance(model.object_types[r.player_id], ObjectifiedFactType) for r in fact.roles):
                    priority = 1
            rows.append((priority, fact.id, role.id))
    return sorted(rows, key=lambda x: (x[0], x[1], x[2]))


def _contextualize_value(model: Model, source: CounterexampleResult) -> ExecutionWitness | None:
    c = model.constraints.get(source.obligation_id)
    if c is None or c.kind != ConstraintKind.VALUE or not c.object_type_id:
        return None
    value_type = model.object_types.get(c.object_type_id)
    if not isinstance(value_type, ValueType):
        return None
    standalone = [iid for iid in _standalone_values(source.population) if source.population.value_types.get(iid) == value_type.id]
    if len(standalone) != 1:
        return None
    invalid_iid = standalone[0]
    invalid_literal = source.population.values[invalid_iid]

    for _priority, fact_id, role_id in _candidate_occurrences(model, value_type.id):
        fact = model.fact_types[fact_id]
        role = next(r for r in fact.roles if r.id == role_id)
        b = _Builder(model)
        try:
            row = b.fact_row(fact_id, 0)
        except Exception:
            continue
        old_iid = row[role.ordinal]
        replacement = list(row)
        replacement[role.ordinal] = invalid_iid
        rows = b.pop.facts.get(fact_id, [])
        replaced = False
        for idx, existing in enumerate(rows):
            if existing == row:
                rows[idx] = tuple(replacement)
                replaced = True
                break
        if not replaced:
            continue
        b.pop.add_value(value_type.id, invalid_iid, invalid_literal)
        _remove_unused_value(b.pop, old_iid)
        try:
            _repair_non_target_context(model, b, source.obligation_id)
        except Exception:
            continue
        violations = validate_population(model, b.pop)
        if not violations or any(v.obligation_id != source.obligation_id for v in violations):
            continue
        if not any(v.obligation_id == source.obligation_id for v in violations):
            continue
        source_members, source_rows = _atom_counts(source.population)
        ctx_members, ctx_rows = _atom_counts(b.pop)
        return ExecutionWitness(
            population=deepcopy(b.pop),
            mode="contextualized_source_core",
            source_obligation_id=source.obligation_id,
            context_added=True,
            context_fact_type_id=fact_id,
            context_role_id=role_id,
            added_membership_count=max(0, ctx_members - source_members),
            added_fact_row_count=max(0, ctx_rows - source_rows),
            note=(
                "The minimal source counterexample contains a standalone invalid value, but the current physical targets store values only through a field/fact occurrence. "
                "Factgraph added one deterministic source-valid occurrence context around the same invalid value and re-ran the source oracle; exactly the original obligation remains violated."
            ),
        )
    return None



def _merge_population(dst: SemanticPopulation, src: SemanticPopulation) -> None:
    for oid, instances in src.memberships.items():
        dst.memberships.setdefault(oid, set()).update(instances)
    for iid, literal in src.values.items():
        if iid in dst.values and dst.values[iid] != literal:
            raise ValueError(f"population merge literal collision for {iid}")
        dst.values[iid] = literal
        dst.value_types[iid] = src.value_types[iid]
    for fid, rows in src.facts.items():
        dest = dst.facts.setdefault(fid, [])
        for row in rows:
            if row not in dest:
                dest.append(row)
    for iid, binding in src.objectifications.items():
        if iid in dst.objectifications and dst.objectifications[iid] != binding:
            raise ValueError(f"population merge objectification collision for {iid}")
        dst.objectifications[iid] = binding


def _objectified_instances(model: Model, pop: SemanticPopulation, obj: ObjectifiedFactType) -> set[str]:
    instances = set(pop.memberships.get(obj.id, set()))
    for fact in model.fact_types.values():
        for role in fact.roles:
            if role.player_id != obj.id:
                continue
            for row in pop.facts.get(fact.id, []):
                if len(row) > role.ordinal:
                    instances.add(row[role.ordinal])
    return instances


def _add_required_objectified_fields(
    model: Model,
    pop: SemanticPopulation,
    obj: ObjectifiedFactType,
    instance_id: str,
    target_obligation_id: str,
    variant: int,
) -> None:
    target = model.constraints.get(target_obligation_id)
    for hint in sorted((h for h in model.field_hints.values() if h.owner_object_type_id == obj.id and h.required), key=lambda h: h.field_fact_id):
        if target is not None and target.kind == ConstraintKind.MANDATORY and target.fact_type_id == hint.field_fact_id:
            # Missing this field is the intended violation.
            continue
        fact = model.fact_types[hint.field_fact_id]
        owner_role = next(r for r in fact.roles if r.player_id == obj.id)
        value_role = next(r for r in fact.roles if r.player_id == hint.value_type_id)
        if any(row[owner_role.ordinal] == instance_id for row in pop.facts.get(fact.id, [])):
            continue
        vt = model.object_types[hint.value_type_id]
        assert isinstance(vt, ValueType)
        literal = _valid_literal(model, vt, 800 + variant)
        value_iid = f"value:{vt.id}:{repr(literal)}"
        pop.add_value(vt.id, value_iid, literal)
        row = tuple(instance_id if r.id == owner_role.id else value_iid for r in fact.roles)
        pop.add_fact(fact.id, row)


def _append_valid_objectified_fact_row(model: Model, pop: SemanticPopulation, obj: ObjectifiedFactType, variant: int) -> int:
    b = _Builder(model)
    row = b.fact_row(obj.fact_type_id, 500 + variant)
    before = len(pop.facts.get(obj.fact_type_id, []))
    _merge_population(pop, b.pop)
    rows = pop.facts.get(obj.fact_type_id, [])
    # _merge_population deduplicates context rows; high variants should make the
    # row novel, but find it deterministically rather than relying on that.
    for idx in range(before, len(rows)):
        if rows[idx] == row:
            return idx
    for idx, existing in enumerate(rows):
        if existing == row:
            return idx
    raise AssertionError("merged objectified fact row is not present")


def _contextualize_objectifications(
    model: Model,
    population: SemanticPopulation,
    target_obligation_id: str,
) -> tuple[SemanticPopulation, dict[str, Any]] | None:
    pop = deepcopy(population)
    touched = []
    for obj in sorted((o for o in model.object_types.values() if isinstance(o, ObjectifiedFactType)), key=lambda o: o.id):
        instances = _objectified_instances(model, pop, obj)
        rows = pop.facts.get(obj.fact_type_id, [])
        if not instances and not rows:
            continue
        touched.append(obj)

        # Preserve any existing valid bindings, then allocate deterministic
        # support instances/occurrences for every unbound side.
        bound_instances = {
            iid for iid, (oid, fid, row_index) in pop.objectifications.items()
            if oid == obj.id and fid == obj.fact_type_id and 0 <= row_index < len(pop.facts.get(fid, []))
        }
        bound_rows = {
            row_index for _iid, (oid, fid, row_index) in pop.objectifications.items()
            if oid == obj.id and fid == obj.fact_type_id and 0 <= row_index < len(pop.facts.get(fid, []))
        }

        instances = set(instances)
        unbound_instances = sorted(instances - bound_instances)
        available_rows = [i for i in range(len(pop.facts.get(obj.fact_type_id, []))) if i not in bound_rows]

        while len(available_rows) < len(unbound_instances):
            idx = _append_valid_objectified_fact_row(model, pop, obj, len(available_rows) + len(bound_rows) + 1)
            if idx not in bound_rows and idx not in available_rows:
                available_rows.append(idx)

        for iid, row_index in zip(unbound_instances, sorted(available_rows)):
            pop.bind_objectification(obj.id, iid, obj.fact_type_id, row_index)
            bound_instances.add(iid); bound_rows.add(row_index)

        # Any remaining source fact occurrence needs a support objectified
        # instance so object-owned fields can be represented on physical targets.
        for row_index in range(len(pop.facts.get(obj.fact_type_id, []))):
            if row_index in bound_rows:
                continue
            iid = f"execution:objectified:{obj.id}:{row_index + 1}"
            pop.bind_objectification(obj.id, iid, obj.fact_type_id, row_index)
            bound_instances.add(iid); bound_rows.add(row_index)

        for serial, iid in enumerate(sorted(bound_instances)):
            _add_required_objectified_fields(model, pop, obj, iid, target_obligation_id, serial)

    if not touched:
        return None

    violations = validate_population(model, pop)
    if not violations or any(v.obligation_id != target_obligation_id for v in violations):
        return None
    if not any(v.obligation_id == target_obligation_id for v in violations):
        return None
    original_members, original_rows = _atom_counts(population)
    ctx_members, ctx_rows = _atom_counts(pop)
    return pop, {
        "objects": [o.id for o in touched],
        "added_membership_count": max(0, ctx_members - original_members),
        "added_fact_row_count": max(0, ctx_rows - original_rows),
        "note": (
            "Factgraph added target-independent occurrence identity and valid surrounding facts/required objectified fields. "
            "The source oracle was re-run and still reports exactly the original obligation as violated."
        ),
    }

def prepare_execution_witness(model: Model, source: CounterexampleResult) -> ExecutionWitness:
    working = deepcopy(source.population)
    notes: list[str] = []
    context_fact_type_id: str | None = None
    context_role_id: str | None = None
    added_memberships = 0
    added_rows = 0

    value_context = _contextualize_value(model, source)
    if value_context is not None:
        working = value_context.population
        notes.append(value_context.note)
        context_fact_type_id = value_context.context_fact_type_id
        context_role_id = value_context.context_role_id
        added_memberships += value_context.added_membership_count
        added_rows += value_context.added_fact_row_count

    object_context = _contextualize_objectifications(model, working, source.obligation_id)
    if object_context is not None:
        before_members, before_rows = _atom_counts(working)
        working, meta = object_context
        after_members, after_rows = _atom_counts(working)
        added_memberships += max(0, after_members - before_members)
        added_rows += max(0, after_rows - before_rows)
        notes.append(meta["note"])

    if notes:
        violations = validate_population(model, working)
        if violations and all(v.obligation_id == source.obligation_id for v in violations):
            return ExecutionWitness(
                population=working,
                mode="contextualized_source_core",
                source_obligation_id=source.obligation_id,
                context_added=True,
                context_fact_type_id=context_fact_type_id,
                context_role_id=context_role_id,
                added_membership_count=added_memberships,
                added_fact_row_count=added_rows,
                note=" ".join(notes),
            )

    return ExecutionWitness(
        population=deepcopy(source.population),
        mode="exact_minimal_source",
        source_obligation_id=source.obligation_id,
        context_added=False,
        note="The physical execution population is exactly the locally irreducible source semantic counterexample.",
    )
