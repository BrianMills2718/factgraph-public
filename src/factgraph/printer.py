from __future__ import annotations

import json

from .ids import explicit_token
from .model import ConstraintKind, EntityType, Model, ValueType


def _identity_suffix(kind: str, element_id: str) -> str:
    token = explicit_token(kind, element_id)
    return f" identity {json.dumps(token)}" if token is not None else ""


def _type_name(model: Model, object_id: str) -> str:
    return model.object_types[object_id].name


def _field_lines(model: Model, owner_id: str, indent: str = "    ") -> list[str]:
    hints = sorted(
        (h for h in model.field_hints.values() if h.owner_object_type_id == owner_id),
        key=lambda h: h.field_name,
    )
    out: list[str] = []
    for h in hints:
        prefix = "id " if h.identifier_component else ""
        suffix = "" if h.required else "?"
        identity = _identity_suffix("fact", h.field_fact_id)
        out.append(f"{indent}{prefix}{h.field_name}: {_type_name(model, h.value_type_id)}{suffix}{identity}")
    return out


def _literal(v) -> str:
    if isinstance(v, str):
        return json.dumps(v)
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def _role_names(fact, role_ids: tuple[str, ...]) -> list[str]:
    by_id = {r.id: r.name for r in fact.roles}
    return [by_id[rid] for rid in role_ids]


def _role_seq_text(model: Model, fact_id: str, role_ids: tuple[str, ...]) -> str:
    fact = model.fact_types[fact_id]
    return f"{fact.name}({', '.join(_role_names(fact, role_ids))})"


def print_model(model: Model) -> str:
    lines: list[str] = [f"model {model.name}{_identity_suffix('model', model.id)} {{"]

    values = sorted((o for o in model.object_types.values() if isinstance(o, ValueType)), key=lambda o: o.name)
    entities = sorted((o for o in model.object_types.values() if isinstance(o, EntityType)), key=lambda o: o.name)
    source_fact_ids = set(model.fact_types) - set(model.field_hints)
    source_facts = sorted((model.fact_types[fid] for fid in source_fact_ids), key=lambda f: f.name)

    if values:
        lines.append("")
        for v in values:
            vcs = model.constraints_for_value(v.id)
            if not vcs:
                lines.append(f"  value {v.name}: {v.scalar_kind}{_identity_suffix('value', v.id)}")
                continue
            lines.append(f"  value {v.name}: {v.scalar_kind}{_identity_suffix('value', v.id)} {{")
            for c in vcs:
                spec = c.value_spec or {}
                if spec.get("kind") == "range":
                    lines.append(f"    range({_literal(spec['min'])}, {_literal(spec['max'])})")
                elif spec.get("kind") == "oneof":
                    vals = ", ".join(_literal(x) for x in spec.get("values", []))
                    lines.append(f"    oneof({vals})")
            lines.append("  }")

    if entities:
        lines.append("")
        for e in entities:
            lines.append(f"  entity {e.name}{_identity_suffix('entity', e.id)} {{")
            lines.extend(_field_lines(model, e.id, "    "))
            lines.append("  }")

    subtype_constraints = sorted(
        (c for c in model.constraints.values() if c.kind == ConstraintKind.SUBTYPE), key=lambda c: c.id
    )
    if subtype_constraints:
        lines.append("")
        for c in subtype_constraints:
            sub = model.object_types[c.subtype_id].name
            sup = model.object_types[c.supertype_id].name
            lines.append(f"  subtype {sub} is {sup}")

    if source_facts:
        lines.append("")
        for fact in source_facts:
            role_parts = [
                f"{r.name}: {_type_name(model, r.player_id)}{_identity_suffix('role', r.id)}"
                for r in sorted(fact.roles, key=lambda r: r.ordinal)
            ]
            obj = model.objectification_for_fact(fact.id)
            objectify = (
                f" objectify {obj.name}{_identity_suffix('objectified', obj.id)}"
                if obj is not None
                else ""
            )
            lines.append(
                f"  fact {fact.name}{_identity_suffix('fact', fact.id)}({', '.join(role_parts)}){objectify} {{"
            )

            if obj is not None:
                lines.extend(_field_lines(model, obj.id, "    "))

            readings = sorted(
                (r for r in model.readings.values() if r.fact_type_id == fact.id), key=lambda r: r.id
            )
            for reading in readings:
                lines.append(f"    reading {json.dumps(reading.template)}")

            constraints = model.constraints_for_fact(fact.id)
            for c in constraints:
                if c.kind == ConstraintKind.UNIQUENESS:
                    lines.append(f"    unique({', '.join(_role_names(fact, c.role_ids))})")
                elif c.kind == ConstraintKind.MANDATORY:
                    lines.append(f"    mandatory({_role_names(fact, c.role_ids)[0]})")
                elif c.kind == ConstraintKind.FREQUENCY:
                    names = _role_names(fact, c.role_ids)
                    args = [*names, str(c.min_frequency), str(c.max_frequency)]
                    lines.append(f"    frequency({', '.join(args)})")
                elif c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
                    lines.append(f"    unordered({', '.join(_role_names(fact, c.role_ids))})")
                elif c.kind == ConstraintKind.RING and c.ring_kind == "symmetric":
                    lines.append("    symmetric")
            lines.append("  }")

    set_constraints = sorted(
        (
            c
            for c in model.constraints.values()
            if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}
        ),
        key=lambda c: c.id,
    )
    if set_constraints:
        lines.append("")
        for c in set_constraints:
            left = _role_seq_text(model, c.fact_type_id, c.role_ids)
            right = _role_seq_text(model, c.target_fact_type_id, c.target_role_ids)
            lines.append(f"  {c.kind.value} {left} {right}")

    if model.samples:
        lines.append("")
        by_id = {f.id: f for f in model.fact_types.values()}
        for sample in sorted(model.samples, key=lambda s: (by_id[s.fact_type_id].name, s.values)):
            vals = ", ".join(json.dumps(v) for v in sample.values)
            lines.append(f"  sample {by_id[sample.fact_type_id].name}({vals})")

    if model.analyses:
        lines.append("")
        for name, args in sorted(model.analyses):
            if args:
                lines.append(f"  analysis {name}({', '.join(args)})")
            else:
                lines.append(f"  analysis {name}")

    lines.append("}")
    return "\n".join(lines) + "\n"
