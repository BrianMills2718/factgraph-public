from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from .model import ConstraintKind, Model, ValueType
from .reporting import Diagnostic, Severity


def _sample_scalar(raw: str, kind: str):
    try:
        if kind == "Int":
            return int(raw)
        if kind in {"Decimal", "Float"}:
            return Decimal(raw)
        if kind == "Bool":
            low = raw.lower()
            if low in {"true", "1"}:
                return True
            if low in {"false", "0"}:
                return False
            return raw
    except (ValueError, InvalidOperation):
        return raw
    return raw


def _value_allowed(model: Model, value_type: ValueType, raw: str) -> bool:
    value = _sample_scalar(raw, value_type.scalar_kind)
    for c in model.constraints_for_value(value_type.id):
        spec = c.value_spec or {}
        kind = spec.get("kind")
        if kind == "range":
            lo = Decimal(str(spec["min"]))
            hi = Decimal(str(spec["max"]))
            try:
                v = Decimal(str(value))
            except InvalidOperation:
                return False
            if not (lo <= v <= hi):
                return False
        elif kind == "oneof":
            allowed = spec.get("values", [])
            if value_type.scalar_kind in {"Int", "Decimal", "Float"}:
                normalized = [Decimal(str(x)) for x in allowed]
                try:
                    if Decimal(str(value)) not in normalized:
                        return False
                except InvalidOperation:
                    return False
            else:
                if value not in allowed and str(value) not in {str(x) for x in allowed}:
                    return False
    return True


def _project_rows(model: Model, fact_id: str, role_ids: tuple[str, ...], rows_by_fact: dict[str, list[tuple[str, ...]]]):
    fact = model.fact_types[fact_id]
    positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in role_ids]
    return {tuple(row[p] for p in positions) for row in rows_by_fact.get(fact_id, [])}


def validate_model(model: Model) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    # Referential integrity: one definition, owned by the model.
    dangling = model.dangling_references()
    for ref in dangling:
        code = "IDENTIFIER_UNKNOWN_OBJECT" if ref.code == "CONSTRAINT_UNKNOWN_OBJECT" and model.constraints[ref.element_id].kind == ConstraintKind.PREFERRED_IDENTIFIER else ref.code
        out.append(Diagnostic(code, Severity.ERROR, ref.message, ref.element_id))
    # Elements with a dangling reference are excluded from population checks
    # below so the report stays a report instead of a KeyError.
    broken = {ref.element_id for ref in dangling}

    # Structural role invariants.
    for fact in sorted(model.fact_types.values(), key=lambda f: f.id):
        names = [r.name for r in fact.roles]
        if not fact.roles:
            out.append(Diagnostic("FACT_ARITY_ZERO", Severity.ERROR, f"fact {fact.name} has no roles", fact.id))
        if len(names) != len(set(names)):
            out.append(Diagnostic("DUPLICATE_ROLE", Severity.ERROR, f"fact {fact.name} has duplicate role names", fact.id))

    for reading in model.readings.values():
        fact = model.fact_types.get(reading.fact_type_id)
        if fact is None:
            continue
        placeholders = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", reading.template)
        unknown = set(placeholders) - {r.name for r in fact.roles}
        if unknown:
            out.append(Diagnostic("READING_UNKNOWN_ROLE", Severity.ERROR, f"reading uses unknown role(s): {sorted(unknown)}", reading.id))

    for c in model.constraints.values():
        if c.id in broken:
            continue
        if c.kind == ConstraintKind.VALUE:
            if not isinstance(model.object_types.get(c.object_type_id or ""), ValueType):
                out.append(Diagnostic("VALUE_CONSTRAINT_BAD_TYPE", Severity.ERROR, "value constraint must target a value type", c.id))
            continue
        fact = model.fact_types.get(c.fact_type_id or "")
        if fact is not None:
            if c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
                players = {r.player_id for r in fact.roles if r.id in c.role_ids}
                if len(c.role_ids) < 2 or len(players) != 1:
                    out.append(Diagnostic("BAD_UNORDERED_GROUP", Severity.ERROR, "unordered role group must contain at least two roles with the same player type", c.id))
            if c.kind == ConstraintKind.RING and c.ring_kind == "symmetric":
                if len(fact.roles) != 2 or fact.roles[0].player_id != fact.roles[1].player_id:
                    out.append(Diagnostic("BAD_SYMMETRIC_RING", Severity.ERROR, "symmetric ring constraint requires binary same-player fact", c.id))
            if c.kind == ConstraintKind.FREQUENCY:
                if not c.role_ids or c.min_frequency is None or c.max_frequency is None or c.min_frequency < 0 or c.max_frequency < c.min_frequency:
                    out.append(Diagnostic("BAD_FREQUENCY", Severity.ERROR, "invalid frequency constraint", c.id))
            if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
                if len(c.role_ids) != len(c.target_role_ids):
                    out.append(Diagnostic("SET_CONSTRAINT_ARITY", Severity.ERROR, "set constraint role sequences differ in arity", c.id))
        if c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
            for ffid in c.field_fact_ids:
                if ffid not in model.field_hints:
                    out.append(Diagnostic("IDENTIFIER_NOT_FIELD", Severity.ERROR, "identifier component is not a field-origin fact", c.id))

    # Sample population: set semantics, value constraints, uniqueness and frequency.
    indexed_rows: dict[str, list[tuple[int, tuple[str, ...], int | None]]] = defaultdict(list)
    rows_by_fact: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for idx, sample in enumerate(model.samples):
        fact = model.fact_types.get(sample.fact_type_id)
        if fact is None:
            continue  # reported as SAMPLE_UNKNOWN_FACT above
        if len(sample.values) != len(fact.roles):
            out.append(Diagnostic("SAMPLE_ARITY", Severity.ERROR, f"sample for {fact.name} has wrong arity", fact.id, sample.source_line))
            continue
        values = list(sample.values)
        for pos, role in enumerate(fact.roles):
            player = model.object_types.get(role.player_id)
            if isinstance(player, ValueType) and not _value_allowed(model, player, values[pos]):
                out.append(Diagnostic("VALUE_CONSTRAINT_VIOLATION", Severity.ERROR, f"sample value {values[pos]!r} violates {player.name} constraint", role.id, sample.source_line))
        for c in model.constraints_for_fact(fact.id, ConstraintKind.UNORDERED_ROLE_GROUP):
            if c.id in broken:
                continue
            positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in c.role_ids]
            sorted_values = sorted(values[p] for p in positions)
            for p, v in zip(sorted(positions), sorted_values):
                values[p] = v
        row = tuple(values)
        indexed_rows[fact.id].append((idx, row, sample.source_line))
        rows_by_fact[fact.id].append(row)

    for fact_id, rows in indexed_rows.items():
        fact = model.fact_types[fact_id]
        seen: dict[tuple[str, ...], int] = {}
        for idx, row, _line in rows:
            if row in seen:
                out.append(Diagnostic("DUPLICATE_FACT", Severity.ERROR, f"sample population repeats the same {fact.name} fact (set semantics)", fact.id))
            else:
                seen[row] = idx
        for c in model.constraints_for_fact(fact_id, ConstraintKind.UNIQUENESS):
            if c.id in broken:
                continue
            positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in c.role_ids]
            u_seen: dict[tuple[str, ...], tuple[str, ...]] = {}
            for _, row, _ in rows:
                key = tuple(row[p] for p in positions)
                previous = u_seen.get(key)
                if previous is not None and previous != row:
                    role_names = [fact.roles[p].name for p in positions]
                    out.append(Diagnostic("UNIQUENESS_VIOLATION", Severity.ERROR, f"sample population violates {fact.name} uniqueness over roles {role_names}: {key}", c.id))
                else:
                    u_seen[key] = row
        for c in model.constraints_for_fact(fact_id, ConstraintKind.FREQUENCY):
            if c.id in broken:
                continue
            positions = [next(i for i, r in enumerate(fact.roles) if r.id == rid) for rid in c.role_ids]
            counts = Counter(tuple(row[p] for p in positions) for _, row, _ in rows)
            for key, count in counts.items():
                if count < (c.min_frequency or 0) or count > (c.max_frequency if c.max_frequency is not None else count):
                    out.append(Diagnostic("FREQUENCY_VIOLATION", Severity.ERROR, f"sample population violates {fact.name} frequency {c.min_frequency}..{c.max_frequency} for {key}: observed {count}", c.id))

    # Cross-fact population constraints.
    for c in sorted(model.constraints.values(), key=lambda x: x.id):
        if c.kind not in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION} or c.id in broken:
            continue
        left = _project_rows(model, c.fact_type_id, c.role_ids, rows_by_fact)
        right = _project_rows(model, c.target_fact_type_id, c.target_role_ids, rows_by_fact)
        if c.kind == ConstraintKind.SUBSET and not left.issubset(right):
            missing = sorted(left - right)
            out.append(Diagnostic("SUBSET_VIOLATION", Severity.ERROR, f"sample population violates subset constraint; missing in target: {missing}", c.id))
        elif c.kind == ConstraintKind.EQUALITY and left != right:
            out.append(Diagnostic("EQUALITY_VIOLATION", Severity.ERROR, f"sample population violates equality constraint; left-only={sorted(left-right)} right-only={sorted(right-left)}", c.id))
        elif c.kind == ConstraintKind.EXCLUSION and left.intersection(right):
            out.append(Diagnostic("EXCLUSION_VIOLATION", Severity.ERROR, f"sample population violates exclusion constraint; overlap={sorted(left.intersection(right))}", c.id))

    return sorted(out, key=lambda d: (d.severity.value, d.code, d.element_id or ""))
