from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from ..normalize import normalize_model
from ..parser import parse_model
from ..printer import print_model
from ..model import Model


BUILTIN_SCALARS = {
    "String": "String",
    "Integer": "Int",
    "Decimal": "Decimal",
    "Float": "Float",
    "Boolean": "Bool",
    "Date": "Date",
    "DateTime": "Timestamp",
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
            raise ValueError("Ossie YAML input requires PyYAML; install factgraph[interchange], or provide JSON") from exc
        raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("Ossie document must be a mapping/object")
    return raw


def _ident(raw: str, fallback: str = "X") -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(raw))
    if not s:
        s = fallback
    if not re.match(r"[A-Za-z_]", s[0]):
        s = "_" + s
    return s


def _role_base(name: str) -> str:
    s = _ident(name, "role")
    return s[:1].lower() + s[1:]


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _resolve_value_scalar(name: str, concepts: dict[str, dict[str, Any]], stack: tuple[str, ...] = ()) -> str | None:
    if name in BUILTIN_SCALARS:
        return BUILTIN_SCALARS[name]
    if name in stack:
        return None
    concept = concepts.get(name)
    if not concept or concept.get("type") != "ValueType":
        return None
    roots = []
    for parent in concept.get("extends") or []:
        resolved = _resolve_value_scalar(str(parent), concepts, stack + (name,))
        if resolved:
            roots.append(resolved)
    roots = sorted(set(roots))
    return roots[0] if len(roots) == 1 else None


def _transform_verbalization(template: str, role_lookup: dict[tuple[str, str | None], str], first_concept: str) -> tuple[str | None, str | None]:
    """Translate Ossie placeholders `{Concept}` / `{Concept:role}` to Factgraph `{role}`."""
    error = None

    def repl(match: re.Match[str]) -> str:
        nonlocal error
        token = match.group(1)
        if ":" in token:
            concept, explicit = token.split(":", 1)
            key = (concept, explicit)
        else:
            concept, explicit = token, None
            key = (concept, None)
        role = role_lookup.get(key)
        if role is None and concept == first_concept and explicit is None:
            role = role_lookup.get((first_concept, "__first__"))
        if role is None:
            error = f"could not resolve verbalization placeholder {{{token}}}"
            return match.group(0)
        return "{" + role + "}"

    transformed = re.sub(r"\{([^{}]+)\}", repl, template)
    return (None, error) if error else (transformed, None)


def import_ossie(text: str) -> ImportResult:
    raw = _load_text(text)
    model_name_raw = str(raw.get("name") or "ImportedOssie")
    model_name = _ident(model_name_raw, "ImportedOssie")
    components = raw.get("ontology")
    if not isinstance(components, list):
        raise ValueError("Ossie ontology document must contain an `ontology` list")

    concepts: dict[str, dict[str, Any]] = {}
    for idx, comp in enumerate(components):
        if not isinstance(comp, dict) or not comp.get("concept"):
            raise ValueError(f"ontology[{idx}] must be an object with `concept`")
        name = str(comp["concept"])
        if name in concepts:
            raise ValueError(f"duplicate Ossie concept {name!r}")
        concepts[name] = comp

    issues: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    concept_names = {name: _ident(name) for name in concepts}
    if len(set(concept_names.values())) != len(concept_names):
        raise ValueError("Ossie concept names collide after Factgraph identifier normalization")

    # Pre-scan simple identifying relationships that can map faithfully to Factgraph fields.
    id_field_relations: dict[tuple[str, str], dict[str, Any]] = {}
    for cname, comp in concepts.items():
        identify = [str(x) for x in (comp.get("identify_by") or [])]
        rel_by_name = {str(r.get("name")): r for r in (comp.get("relationships") or []) if isinstance(r, dict) and r.get("name")}
        for relname in identify:
            rel = rel_by_name.get(relname)
            path = f"concept:{cname}.identify_by:{relname}"
            if not rel:
                issues.append({"path": path, "status": "unsupported", "reason": "identifier names a missing relationship"})
                continue
            roles = rel.get("roles") or []
            if len(roles) == 1 and isinstance(roles[0], dict):
                target_name = str(roles[0].get("concept") or "")
                if target_name in concepts and concepts[target_name].get("type") == "ValueType" and _resolve_value_scalar(target_name, concepts):
                    id_field_relations[(cname, relname)] = rel
                    issues.append({"path": path, "status": "preserved", "reason": "binary entity-to-value identifying relationship mapped to Factgraph id field"})
                    continue
            issues.append({"path": path, "status": "unsupported", "reason": "current Factgraph preferred identifiers require field-origin value facts; this identifying relationship is not a simple binary entity-to-value relationship"})

    lines = [f"model {model_name} identity {_q('ossie:model:' + model_name_raw)} {{"]

    # Values first so entity fields can reference them.
    for cname, comp in concepts.items():
        if comp.get("type") != "ValueType":
            continue
        scalar = _resolve_value_scalar(cname, concepts)
        if scalar is None:
            raise ValueError(f"cannot resolve a single supported built-in scalar for Ossie ValueType {cname!r}")
        safe = concept_names[cname]
        lines.append(f"  value {safe}: {scalar} identity {_q('ossie:concept:' + cname)}")
        mappings.append({"source": f"concept:{cname}", "target_identity": f"ossie:concept:{cname}", "kind": "value_type", "status": "preserved"})
        if comp.get("requires"):
            issues.append({"path": f"concept:{cname}.requires", "status": "unsupported", "reason": "Ossie expression-language requires constraints are retained only in the import report in this first adapter"})
        if comp.get("derived_by"):
            issues.append({"path": f"concept:{cname}.derived_by", "status": "unsupported", "reason": "derived concept expressions are outside the current Factgraph semantic kernel"})

    # Entities and simple identifier fields.
    for cname, comp in concepts.items():
        if comp.get("type") != "EntityType":
            continue
        safe = concept_names[cname]
        fields: list[str] = []
        for relname in [str(x) for x in (comp.get("identify_by") or [])]:
            rel = id_field_relations.get((cname, relname))
            if rel is None:
                continue
            target_name = str(rel["roles"][0]["concept"])
            field_name = _role_base(relname)
            fields.append(f"    id {field_name}: {concept_names[target_name]} identity {_q('ossie:relationship:' + cname + '.' + relname)}")
        if fields:
            lines.append(f"  entity {safe} identity {_q('ossie:concept:' + cname)} {{")
            lines.extend(fields)
            lines.append("  }")
        else:
            lines.append(f"  entity {safe} identity {_q('ossie:concept:' + cname)} {{}}")
        mappings.append({"source": f"concept:{cname}", "target_identity": f"ossie:concept:{cname}", "kind": "entity_type", "status": "preserved"})
        if comp.get("requires"):
            issues.append({"path": f"concept:{cname}.requires", "status": "unsupported", "reason": "Ossie expression-language requires constraints are not yet translated"})
        if comp.get("derived_by"):
            issues.append({"path": f"concept:{cname}.derived_by", "status": "unsupported", "reason": "derived concept populations are not yet translated"})

    # Single inheritance where representable.
    for cname, comp in concepts.items():
        if comp.get("type") != "EntityType":
            continue
        parents = [str(x) for x in (comp.get("extends") or []) if str(x) != "Any"]
        known_entity_parents = [p for p in parents if p in concepts and concepts[p].get("type") == "EntityType"]
        if len(known_entity_parents) == 1:
            p = known_entity_parents[0]
            lines.append(f"  subtype {concept_names[cname]} is {concept_names[p]}")
            issues.append({"path": f"concept:{cname}.extends", "status": "preserved", "reason": f"single entity supertype {p} mapped to Factgraph subtype"})
        elif len(known_entity_parents) > 1:
            issues.append({"path": f"concept:{cname}.extends", "status": "lossy_dropped", "reason": "Factgraph currently supports a single entity supertype; multiple inheritance was not guessed"})

    # Relationship facts. Identifying relationships already mapped to fields are skipped here.
    fact_names: set[str] = set()
    for cname, comp in concepts.items():
        for rel_idx, rel in enumerate(comp.get("relationships") or []):
            if not isinstance(rel, dict) or not rel.get("name"):
                issues.append({"path": f"concept:{cname}.relationships[{rel_idx}]", "status": "unsupported", "reason": "relationship missing name"})
                continue
            relname = str(rel["name"])
            if (cname, relname) in id_field_relations:
                # Reading provenance is not currently attached to field-origin facts.
                if rel.get("verbalizes"):
                    issues.append({"path": f"relationship:{cname}.{relname}.verbalizes", "status": "metadata_only", "reason": "identifier relationship was normalized to a field; verbalization remains in the import report"})
                continue
            fact_name = _ident(f"{cname}__{relname}")
            if fact_name in fact_names:
                raise ValueError(f"relationship names collide after normalization at {cname}.{relname}")
            fact_names.add(fact_name)
            role_specs: list[tuple[str, str, str, tuple[str, str | None]]] = []
            used_roles: set[str] = set()
            first_role = _role_base(cname)
            used_roles.add(first_role)
            role_specs.append((first_role, concept_names[cname], f"ossie:role:{cname}.{relname}:0", (cname, "__first__")))
            role_lookup: dict[tuple[str, str | None], str] = {(cname, "__first__"): first_role, (cname, None): first_role}
            for idx, r in enumerate(rel.get("roles") or [], start=1):
                if not isinstance(r, dict) or not r.get("concept"):
                    raise ValueError(f"relationship {cname}.{relname} role {idx} is missing concept")
                player = str(r["concept"])
                if player not in concepts and player not in BUILTIN_SCALARS:
                    raise ValueError(f"relationship {cname}.{relname} references unknown concept {player!r}")
                # Built-ins referenced directly get an implicit local value declaration.
                if player in BUILTIN_SCALARS and player not in concept_names:
                    implicit = _ident("Ossie" + player)
                    concept_names[player] = implicit
                    # Insertions this late would violate declaration order, so direct built-ins are currently blocked.
                    raise ValueError(f"relationship {cname}.{relname} directly references built-in concept {player}; declare a named Ossie ValueType for this first adapter")
                explicit_name = str(r.get("name")) if r.get("name") is not None else None
                base = _role_base(explicit_name or player)
                role_name = base
                suffix = 2
                while role_name in used_roles:
                    role_name = f"{base}{suffix}"; suffix += 1
                used_roles.add(role_name)
                role_specs.append((role_name, concept_names[player], f"ossie:role:{cname}.{relname}:{idx}", (player, explicit_name)))
                # Unnamed same-player roles become ambiguous in Ossie too; explicit role names win.
                if explicit_name:
                    role_lookup[(player, explicit_name)] = role_name
                elif (player, None) not in role_lookup:
                    role_lookup[(player, None)] = role_name
            lines.append(f"  fact {fact_name} identity {_q('ossie:relationship:' + cname + '.' + relname)}(")
            for idx, (rname, player, rid, _key) in enumerate(role_specs):
                comma = "," if idx < len(role_specs) - 1 else ""
                lines.append(f"    {rname}: {player} identity {_q(rid)}{comma}")
            lines.append("  ) {")
            readings_added = 0
            for verbal in rel.get("verbalizes") or []:
                transformed, err = _transform_verbalization(str(verbal), role_lookup, cname)
                if err:
                    issues.append({"path": f"relationship:{cname}.{relname}.verbalizes", "status": "unsupported", "reason": err, "source": str(verbal)})
                    continue
                # Factgraph currently stores one canonical reading per fact. Preserve the first, report extras.
                if readings_added == 0:
                    lines.append(f"    reading {_q(transformed or '')}")
                    readings_added += 1
                else:
                    issues.append({"path": f"relationship:{cname}.{relname}.verbalizes", "status": "metadata_only", "reason": "Factgraph currently retains one canonical reading; additional Ossie verbalizations remain in the import report", "source": str(verbal)})
            mult = rel.get("multiplicity")
            role_names = [x[0] for x in role_specs]
            if mult == "ManyToOne" and len(role_names) >= 2:
                lines.append(f"    unique({', '.join(role_names[:-1])})")
                issues.append({"path": f"relationship:{cname}.{relname}.multiplicity", "status": "preserved", "reason": "ManyToOne mapped to uniqueness of all roles except the final role"})
            elif mult == "OneToOne" and len(role_names) == 2:
                lines.append(f"    unique({role_names[0]})")
                lines.append(f"    unique({role_names[1]})")
                issues.append({"path": f"relationship:{cname}.{relname}.multiplicity", "status": "preserved", "reason": "binary OneToOne mapped to uniqueness in both directions"})
            elif mult:
                issues.append({"path": f"relationship:{cname}.{relname}.multiplicity", "status": "unsupported", "reason": f"unsupported multiplicity {mult!r} for arity {len(role_names)}"})
            if rel.get("requires"):
                issues.append({"path": f"relationship:{cname}.{relname}.requires", "status": "unsupported", "reason": "Ossie relationship expression constraints are not yet translated"})
            if rel.get("derived_by"):
                issues.append({"path": f"relationship:{cname}.{relname}.derived_by", "status": "unsupported", "reason": "derived relationships are not yet translated"})
            lines.append("  }")
            mappings.append({"source": f"relationship:{cname}.{relname}", "target_identity": f"ossie:relationship:{cname}.{relname}", "kind": "fact_type", "status": "preserved"})

    lines.append("}")
    generated = "\n".join(lines) + "\n"
    model = normalize_model(parse_model(generated))
    unsupported = [x for x in issues if x["status"] in {"unsupported", "lossy_dropped"}]
    report = {
        "format": "factgraph-ossie-import-report-v1",
        "source_format": "apache-ossie-ontology",
        "source_name": model_name_raw,
        "model": model.name,
        "status": "imported_with_gaps" if unsupported else "imported",
        "mappings": sorted(mappings, key=lambda x: (x["source"], x["kind"])),
        "issues": sorted(issues, key=lambda x: (x["path"], x["status"], x.get("reason", ""))),
        "unsupported_or_lossy_count": len(unsupported),
        "contract": "Unsupported source semantics are reported; the importer does not invent replacements for unhandled Ossie expressions, multiple inheritance, or complex identifiers.",
    }
    return ImportResult(model, report, print_model(model))
