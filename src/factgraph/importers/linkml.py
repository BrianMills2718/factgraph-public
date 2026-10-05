from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from ..model import Model
from ..normalize import normalize_model
from ..parser import parse_model
from ..printer import print_model


SCALAR_RANGES = {
    "string": "String",
    "str": "String",
    "integer": "Int",
    "int": "Int",
    "float": "Float",
    "double": "Float",
    "decimal": "Decimal",
    "boolean": "Bool",
    "bool": "Bool",
    "date": "Date",
    "datetime": "Timestamp",
    "uriorcurie": "String",
    "uri": "String",
    "ncname": "String",
}


@dataclass(frozen=True)
class ImportResult:
    model: Model
    report: dict[str, Any]
    generated_source: str


def _load_text(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ValueError("LinkML YAML input requires PyYAML; install factgraph[interchange], or provide JSON") from exc
        raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("LinkML schema must be a mapping/object")
    return raw


def _ident(raw: str, fallback: str = "X") -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(raw))
    if not s:
        s = fallback
    if not re.match(r"[A-Za-z_]", s[0]):
        s = "_" + s
    return s


def _lower_ident(raw: str, fallback: str = "field") -> str:
    s = _ident(raw, fallback)
    return s[:1].lower() + s[1:]


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _merged_slot(global_slots: dict[str, Any], slot_name: str, local: dict[str, Any] | None = None) -> dict[str, Any]:
    base = global_slots.get(slot_name) or {}
    if not isinstance(base, dict):
        base = {}
    out = dict(base)
    if local:
        out.update(local)
    return out


def _custom_type_scalars(raw: dict[str, Any]) -> dict[str, str]:
    types = raw.get("types") or {}
    if not isinstance(types, dict):
        return {}
    resolved: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for name, spec in types.items():
            if name in resolved or not isinstance(spec, dict):
                continue
            candidates = [spec.get("typeof"), spec.get("base"), spec.get("range")]
            scalar = None
            for candidate in candidates:
                if not candidate:
                    continue
                key = str(candidate)
                scalar = SCALAR_RANGES.get(key.lower()) or resolved.get(key)
                if scalar:
                    break
            if scalar:
                resolved[str(name)] = scalar
                changed = True
    return resolved


def import_linkml(text: str) -> ImportResult:
    raw = _load_text(text)
    classes = raw.get("classes") or {}
    slots = raw.get("slots") or {}
    enums = raw.get("enums") or {}
    if not isinstance(classes, dict) or not isinstance(slots, dict) or not isinstance(enums, dict):
        raise ValueError("LinkML classes, slots, and enums must be mappings when present")

    source_name = str(raw.get("name") or raw.get("id") or "ImportedLinkML")
    model_name = _ident(source_name, "ImportedLinkML")
    class_names = {str(name): _ident(str(name)) for name in classes}
    if len(set(class_names.values())) != len(class_names):
        raise ValueError("LinkML class names collide after Factgraph identifier normalization")

    issues: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    custom_types = _custom_type_scalars(raw)

    # Enum ValueTypes are globally reusable and preserve permissible values.
    enum_names: dict[str, str] = {}
    lines = [f"model {model_name} identity {_q('linkml:schema:' + source_name)} {{"]
    for enum_name, enum_spec in sorted(enums.items(), key=lambda x: str(x[0])):
        if not isinstance(enum_spec, dict):
            issues.append({"path": f"enums.{enum_name}", "status": "unsupported", "reason": "enum definition is not an object"})
            continue
        safe = _ident(str(enum_name))
        enum_names[str(enum_name)] = safe
        pvs = enum_spec.get("permissible_values") or {}
        if isinstance(pvs, list):
            values = [str(x) for x in pvs]
        elif isinstance(pvs, dict):
            values = [str(x) for x in pvs.keys()]
        else:
            values = []
        lines.append(f"  value {safe}: String identity {_q('linkml:enum:' + str(enum_name))} {{")
        if values:
            lines.append("    oneof(" + ", ".join(_q(v) for v in values) + ")")
        lines.append("  }")
        mappings.append({"source": f"enum:{enum_name}", "target_identity": f"linkml:enum:{enum_name}", "kind": "value_type", "status": "preserved"})
        for unsupported_key in ("include", "code_set", "pv_formula"):
            if enum_spec.get(unsupported_key):
                issues.append({"path": f"enums.{enum_name}.{unsupported_key}", "status": "unsupported", "reason": "dynamic/composed LinkML enum semantics are not translated by the bounded importer"})

    # Precompute effective direct slot definitions. Inherited parent slots remain on
    # the Factgraph supertype and are inherited through subtype semantics.
    class_slot_specs: dict[tuple[str, str], dict[str, Any]] = {}
    inline_attributes: dict[tuple[str, str], dict[str, Any]] = {}
    for class_name, class_spec in classes.items():
        if not isinstance(class_spec, dict):
            raise ValueError(f"LinkML class {class_name!r} must be an object")
        usage = class_spec.get("slot_usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        for slot_name in class_spec.get("slots") or []:
            local = usage.get(slot_name) if isinstance(usage.get(slot_name), dict) else None
            class_slot_specs[(str(class_name), str(slot_name))] = _merged_slot(slots, str(slot_name), local)
        attrs = class_spec.get("attributes") or {}
        if isinstance(attrs, dict):
            for slot_name, spec in attrs.items():
                if isinstance(spec, dict):
                    inline_attributes[(str(class_name), str(slot_name))] = dict(spec)
                    class_slot_specs[(str(class_name), str(slot_name))] = dict(spec)

    # Scalar occurrence types are slot-specific so range/enum/cardinality meaning does
    # not get accidentally shared across unrelated classes using the same primitive.
    scalar_occurrence_type: dict[tuple[str, str], str] = {}
    relation_slots: list[tuple[str, str, dict[str, Any]]] = []
    relation_slot_keys: set[tuple[str, str]] = set()
    for (class_name, slot_name), spec in sorted(class_slot_specs.items()):
        range_name = str(spec.get("range") or raw.get("default_range") or "string")
        if range_name in class_names:
            relation_slots.append((class_name, slot_name, spec))
            relation_slot_keys.add((class_name, slot_name))
            continue
        scalar = SCALAR_RANGES.get(range_name.lower()) or custom_types.get(range_name)
        if range_name in enum_names:
            scalar_occurrence_type[(class_name, slot_name)] = enum_names[range_name]
            continue
        if scalar is None:
            issues.append({"path": f"classes.{class_name}.slots.{slot_name}.range", "status": "unsupported", "reason": f"range {range_name!r} is not a supported scalar, enum, or class"})
            continue
        vt_name = _ident(f"{class_name}_{slot_name}_Value")
        scalar_occurrence_type[(class_name, slot_name)] = vt_name
        lines.append(f"  value {vt_name}: {scalar} identity {_q('linkml:slot-value:' + class_name + '.' + slot_name)} {{")
        lo = spec.get("minimum_value")
        hi = spec.get("maximum_value")
        if lo is not None and hi is not None:
            lines.append(f"    range({json.dumps(lo)}, {json.dumps(hi)})")
        elif lo is not None or hi is not None:
            issues.append({"path": f"classes.{class_name}.slots.{slot_name}", "status": "unsupported", "reason": "Factgraph bounded range currently requires both minimum_value and maximum_value"})
        lines.append("  }")
        mappings.append({"source": f"slot:{class_name}.{slot_name}", "target_identity": f"linkml:slot-value:{class_name}.{slot_name}", "kind": "value_type", "status": "preserved"})
        if spec.get("pattern") or spec.get("structured_pattern"):
            issues.append({"path": f"classes.{class_name}.slots.{slot_name}.pattern", "status": "unsupported", "reason": "regex/structured patterns are not in the current Factgraph value-constraint kernel"})

    # Entities with scalar single-valued slots mapped as field sugar.
    for class_name, class_spec in sorted(classes.items(), key=lambda x: str(x[0])):
        safe_class = class_names[str(class_name)]
        fields: list[str] = []
        for (owner, slot_name), spec in sorted(class_slot_specs.items()):
            if owner != str(class_name) or (owner, slot_name) in relation_slot_keys:
                continue
            vt = scalar_occurrence_type.get((owner, slot_name))
            if vt is None:
                continue
            multivalued = bool(spec.get("multivalued", False))
            if multivalued:
                continue
            field = _lower_ident(slot_name)
            ident = bool(spec.get("identifier") or spec.get("key"))
            required = bool(spec.get("required")) or ident
            prefix = "id " if ident else ""
            optional = "" if required else "?"
            fields.append(f"    {prefix}{field}: {vt}{optional} identity {_q('linkml:slot:' + owner + '.' + slot_name)}")
            mappings.append({"source": f"slot:{owner}.{slot_name}", "target_identity": f"linkml:slot:{owner}.{slot_name}", "kind": "field", "status": "preserved"})
            if spec.get("unique") and not ident:
                issues.append({"path": f"classes.{owner}.slots.{slot_name}.unique", "status": "unsupported", "reason": "non-identifier global slot uniqueness is not represented by Factgraph field sugar in this importer"})
        if fields:
            lines.append(f"  entity {safe_class} identity {_q('linkml:class:' + str(class_name))} {{")
            lines.extend(fields)
            lines.append("  }")
        else:
            lines.append(f"  entity {safe_class} identity {_q('linkml:class:' + str(class_name))} {{}}")
        mappings.append({"source": f"class:{class_name}", "target_identity": f"linkml:class:{class_name}", "kind": "entity_type", "status": "preserved"})

        if class_spec.get("mixins"):
            issues.append({"path": f"classes.{class_name}.mixins", "status": "unsupported", "reason": "LinkML mixin inheritance is not guessed as Factgraph subtype semantics"})
        if class_spec.get("unique_keys"):
            issues.append({"path": f"classes.{class_name}.unique_keys", "status": "unsupported", "reason": "LinkML alternate/compound unique keys are not yet mapped to a non-preferred Factgraph object uniqueness constraint"})
        for key in ("rules", "classification_rules", "tree_root"):
            if class_spec.get(key):
                issues.append({"path": f"classes.{class_name}.{key}", "status": "unsupported", "reason": f"LinkML {key} semantics are outside the bounded importer"})

    # Single class inheritance.
    for class_name, class_spec in sorted(classes.items(), key=lambda x: str(x[0])):
        parent = class_spec.get("is_a") if isinstance(class_spec, dict) else None
        if parent:
            if str(parent) in class_names:
                lines.append(f"  subtype {class_names[str(class_name)]} is {class_names[str(parent)]}")
                issues.append({"path": f"classes.{class_name}.is_a", "status": "preserved", "reason": "LinkML single class inheritance mapped to Factgraph subtype"})
            else:
                issues.append({"path": f"classes.{class_name}.is_a", "status": "unsupported", "reason": f"unknown parent class {parent!r}"})

    # Class-valued and multivalued scalar slots become explicit fact types.
    for (class_name, slot_name), spec in sorted(class_slot_specs.items()):
        range_name = str(spec.get("range") or raw.get("default_range") or "string")
        is_class = range_name in class_names
        multivalued = bool(spec.get("multivalued", False))
        if not is_class and not multivalued:
            continue
        owner = class_names[class_name]
        if is_class:
            target = class_names[range_name]
        else:
            target = scalar_occurrence_type.get((class_name, slot_name))
            if target is None:
                continue
        fact_name = _ident(f"{class_name}_{slot_name}")
        owner_role = _lower_ident(class_name, "owner")
        value_role = _lower_ident(slot_name, "value")
        lines.append(f"  fact {fact_name} identity {_q('linkml:slot-fact:' + class_name + '.' + slot_name)}(")
        lines.append(f"    {owner_role}: {owner} identity {_q('linkml:slot-role:' + class_name + '.' + slot_name + ':owner')},")
        lines.append(f"    {value_role}: {target} identity {_q('linkml:slot-role:' + class_name + '.' + slot_name + ':value')}")
        lines.append("  ) {")
        required = bool(spec.get("required"))
        minimum = spec.get("minimum_cardinality")
        maximum = spec.get("maximum_cardinality")
        if required or (minimum is not None and int(minimum) > 0):
            lines.append(f"    mandatory({owner_role})")
        if not multivalued:
            lines.append(f"    unique({owner_role})")
        elif minimum is not None or maximum is not None:
            lo = int(minimum or 0)
            if maximum is not None:
                lines.append(f"    frequency({owner_role}, {lo}, {int(maximum)})")
            elif lo > 1:
                issues.append({"path": f"classes.{class_name}.slots.{slot_name}.minimum_cardinality", "status": "unsupported", "reason": "Factgraph frequency syntax currently requires a finite maximum; mandatory preserves only the >=1 portion"})
        lines.append("  }")
        mappings.append({"source": f"slot:{class_name}.{slot_name}", "target_identity": f"linkml:slot-fact:{class_name}.{slot_name}", "kind": "fact_type", "status": "preserved"})
        if spec.get("identifier") or spec.get("key"):
            issues.append({"path": f"classes.{class_name}.slots.{slot_name}.identifier", "status": "unsupported", "reason": "class-valued identifiers are not mapped to Factgraph preferred field identifiers"})
        if spec.get("unique"):
            issues.append({"path": f"classes.{class_name}.slots.{slot_name}.unique", "status": "unsupported", "reason": "LinkML unique class/multivalued slot semantics require global uniqueness of the target projection, not yet emitted by this importer"})

    # Top-level features whose semantics we intentionally do not invent.
    for key in ("subsets", "settings"):
        if raw.get(key):
            issues.append({"path": key, "status": "metadata_only", "reason": f"LinkML {key} metadata is not part of the current Factgraph semantic kernel"})

    lines.append("}")
    generated = "\n".join(lines) + "\n"
    model = normalize_model(parse_model(generated))
    unsupported = [x for x in issues if x["status"] in {"unsupported", "lossy_dropped"}]
    report = {
        "format": "factgraph-linkml-import-report-v1",
        "source_format": "linkml-schema",
        "source_name": source_name,
        "model": model.name,
        "status": "imported_with_gaps" if unsupported else "imported",
        "mappings": sorted(mappings, key=lambda x: (x["source"], x["kind"])),
        "issues": sorted(issues, key=lambda x: (x["path"], x["status"], x.get("reason", ""))),
        "unsupported_or_lossy_count": len(unsupported),
        "contract": "The bounded importer preserves simple classes, scalar/enum fields, single inheritance, class-valued slots, requiredness, at-most-one cardinality, and finite multivalued bounds. Unsupported LinkML rules/mixins/complex keys are reported rather than guessed.",
    }
    return ImportResult(model, report, print_model(model))
