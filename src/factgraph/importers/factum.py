from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from ..model import Model
from ..normalize import normalize_model
from ..parser import parse_model
from ..printer import print_model


@dataclass(frozen=True)
class ImportResult:
    model: Model
    report: dict[str, Any]
    generated_source: str


# Factum v2 dataType values are documented by orm-model-2.schema.json.
# The right-hand side is the closest *semantic scalar kind* in Factgraph.
# Entries in APPROXIMATE_DATATYPES require a gap report because Factgraph does
# not carry the additional lexical/generation semantics.
DATATYPES: dict[str, str] = {
    "string": "String",
    "text": "String",
    "integer": "Int",
    "decimal": "Decimal",
    "float": "Float",
    "money": "Decimal",
    "boolean": "Bool",
    "date": "Date",
    "dateTime": "Timestamp",
    "guid": "UUID",
    "autoCounter": "Int",
    "time": "String",
    "binary": "String",
}

APPROXIMATE_DATATYPES: dict[str, str] = {
    "money": "Factgraph Decimal does not carry currency/money semantics",
    "autoCounter": "Factgraph Int does not carry auto-generation semantics",
    "time": "Factgraph has no time-only scalar; imported as opaque String",
    "binary": "Factgraph has no binary scalar; imported as opaque String",
}


def _refmode_datatype(ref_mode: str) -> str:
    """Factum's documented Rmap default type for an entity reference mode.

    This mirrors Factum 0.5.0's `refModeDataType` rule so a source model that
    omits an explicit `dataType` does not get a transport-incompatible witness.
    """
    lower = ref_mode.lower()
    if re.search(r"(nr|no|number|id|count|seq)$", lower):
        return "integer"
    if re.search(r"(date)$", lower):
        return "date"
    if re.search(r"(amount|price|total)$", lower):
        return "money"
    return "string"


def _ident(raw: str, fallback: str = "X") -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(raw))
    if not s:
        s = fallback
    if not re.match(r"[A-Za-z_]", s[0]):
        s = "_" + s
    return s


def _lower_ident(raw: str, fallback: str = "role") -> str:
    s = _ident(raw, fallback)
    return s[:1].lower() + s[1:]


def _q(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _literal(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _source_token(element: dict[str, Any], fallback_id: str) -> tuple[str, str]:
    """Return a cross-revision identity token and how it was chosen.

    Factum's published schema describes meta.guid as stable cross-tool identity.
    We therefore prefer it when present, and otherwise retain the file-local id.
    """
    meta = element.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("guid"), str) and meta["guid"].strip():
        return f"factum:guid:{meta['guid'].strip()}", "meta.guid"
    return f"factum:id:{fallback_id}", "id"


def _allocate(base: str, used: set[str], discriminator: str) -> str:
    candidate = _ident(base)
    if candidate not in used:
        used.add(candidate)
        return candidate
    tail = _ident(discriminator, "x").strip("_") or "x"
    candidate = _ident(f"{base}_{tail}")
    n = 2
    while candidate in used:
        candidate = _ident(f"{base}_{tail}_{n}")
        n += 1
    used.add(candidate)
    return candidate


def _load(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Factum input must be JSON (.orm.json)") from exc
    if not isinstance(raw, dict):
        raise ValueError("Factum model must be a JSON object")
    if not isinstance(raw.get("objectTypes"), list) or not isinstance(raw.get("factTypes"), list):
        raise ValueError("Factum model must contain objectTypes and factTypes arrays")
    version = raw.get("version", 2)
    if version not in {1, 2}:
        raise ValueError(f"unsupported Factum format version {version!r}; bounded importer supports v1/v2")
    return raw


def _reading_text(reading: dict[str, Any], role_name_by_id: dict[str, str]) -> tuple[str | None, str | None]:
    order = reading.get("roleOrder")
    text = reading.get("text")
    if not isinstance(order, list) or not isinstance(text, str):
        return None, "reading requires roleOrder[] and text"
    names: list[str] = []
    for rid in order:
        name = role_name_by_id.get(str(rid))
        if name is None:
            return None, f"reading roleOrder references unknown role {rid!r}"
        names.append(name)

    bad: str | None = None

    def repl(match: re.Match[str]) -> str:
        nonlocal bad
        idx = int(match.group(1))
        if idx >= len(names):
            bad = f"reading placeholder {{{idx}}} exceeds roleOrder length {len(names)}"
            return match.group(0)
        return "{" + names[idx] + "}"

    transformed = re.sub(r"\{([0-9]+)\}", repl, text)
    if bad:
        return None, bad
    return transformed, None


def import_factum(text: str) -> ImportResult:
    """Import the bounded, semantics-preserving subset of Factum ORM v1/v2.

    This adapter is intentionally not a second Factum implementation.  It maps
    only constructs already represented by the Factgraph semantic kernel and
    records every known mismatch in import_report.json.
    """
    raw = _load(text)
    source_name = str(raw.get("name") or "ImportedFactum")
    version = int(raw.get("version", 2))
    issues: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []

    model_token, model_identity_source = _source_token(raw, f"model:{source_name}")
    model_name = _ident(source_name, "ImportedFactum")

    object_rows: dict[str, dict[str, Any]] = {}
    fact_rows: dict[str, dict[str, Any]] = {}
    role_rows: dict[str, tuple[str, dict[str, Any]]] = {}

    seen_ids: set[str] = set()
    for idx, obj in enumerate(raw.get("objectTypes") or []):
        if not isinstance(obj, dict) or not obj.get("id") or not obj.get("name") or obj.get("kind") not in {"entity", "value"}:
            raise ValueError(f"objectTypes[{idx}] must contain id/name and kind entity|value")
        oid = str(obj["id"])
        if oid in seen_ids:
            raise ValueError(f"duplicate Factum id {oid!r}")
        seen_ids.add(oid)
        object_rows[oid] = obj

    for idx, fact in enumerate(raw.get("factTypes") or []):
        if not isinstance(fact, dict) or not fact.get("id"):
            raise ValueError(f"factTypes[{idx}] must contain id")
        fid = str(fact["id"])
        if fid in seen_ids:
            raise ValueError(f"duplicate Factum id {fid!r}")
        seen_ids.add(fid)
        roles = fact.get("roles")
        if not isinstance(roles, list):
            raise ValueError(f"Factum fact {fid!r} must contain roles[]")
        fact_rows[fid] = fact
        for ridx, role in enumerate(roles):
            if not isinstance(role, dict) or not role.get("id"):
                raise ValueError(f"Factum fact {fid!r} role[{ridx}] must contain id")
            rid = str(role["id"])
            if rid in seen_ids:
                raise ValueError(f"duplicate Factum id {rid!r}")
            seen_ids.add(rid)
            if role.get("objectTypeId") is None:
                raise ValueError(f"Factum role {rid!r} is disconnected (objectTypeId is null); incomplete diagrams cannot be audited")
            if str(role["objectTypeId"]) not in object_rows:
                raise ValueError(f"Factum role {rid!r} references unknown object type {role['objectTypeId']!r}")
            role_rows[rid] = (fid, role)

    # Constraint ids and subtype relation ids also occupy Factum's file id space.
    for collection_name in ("subtypeRelations", "constraints"):
        rows = raw.get(collection_name) or []
        if not isinstance(rows, list):
            raise ValueError(f"Factum {collection_name} must be an array")
        for idx, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"{collection_name}[{idx}] must contain id")
            rid = str(row["id"])
            if rid in seen_ids:
                raise ValueError(f"duplicate Factum id {rid!r}")
            seen_ids.add(rid)

    used_names: set[str] = set()
    object_name: dict[str, str] = {}
    object_token: dict[str, str] = {}
    objectification_by_fact: dict[str, str] = {}

    for oid, obj in object_rows.items():
        safe = _allocate(str(obj["name"]), used_names, oid)
        object_name[oid] = safe
        token, source = _source_token(obj, oid)
        object_token[oid] = token
        mappings.append({
            "source": f"objectType:{oid}",
            "source_name": str(obj["name"]),
            "target_identity": token,
            "identity_source": source,
            "kind": "objectified_fact_type" if obj.get("objectifiedFactTypeId") else f"{obj['kind']}_type",
            "status": "preserved",
        })
        ofid = obj.get("objectifiedFactTypeId")
        if ofid is not None:
            ofid = str(ofid)
            if ofid not in fact_rows:
                raise ValueError(f"Factum object type {oid!r} objectifies unknown fact type {ofid!r}")
            if obj.get("kind") != "entity":
                raise ValueError(f"Factum objectification {oid!r} must be an entity object type")
            if ofid in objectification_by_fact:
                raise ValueError(f"Factum fact {ofid!r} is objectified by more than one object type")
            objectification_by_fact[ofid] = oid

    # Subtyping is pre-scanned so reference-mode fields are not redeclared on subtypes.
    subtype_rows = [r for r in (raw.get("subtypeRelations") or []) if isinstance(r, dict)]
    subtype_parents: dict[str, list[dict[str, Any]]] = {}
    for rel in subtype_rows:
        sub = str(rel.get("subtypeId") or "")
        sup = str(rel.get("supertypeId") or "")
        if sub not in object_rows or sup not in object_rows:
            raise ValueError(f"Factum subtype relation {rel.get('id')!r} references unknown object type")
        subtype_parents.setdefault(sub, []).append(rel)

    # Value constraints must be known before emitting value declarations.
    value_fragments: dict[str, list[str]] = {}
    # Local fact constraints keyed by fact id.
    local_fragments: dict[str, list[str]] = {fid: [] for fid in fact_rows}
    set_fragments: list[str] = []

    # Fact/role display names are deterministic but source identity is carried separately.
    fact_name: dict[str, str] = {}
    role_name: dict[str, str] = {}
    fact_token: dict[str, str] = {}
    role_token: dict[str, str] = {}
    for fid, fact in fact_rows.items():
        meta = fact.get("meta") if isinstance(fact.get("meta"), dict) else {}
        preferred_name = meta.get("title") if isinstance(meta.get("title"), str) and meta.get("title") else fid
        fact_name[fid] = _allocate(str(preferred_name), used_names, fid)
        token, source = _source_token(fact, fid)
        fact_token[fid] = token
        mappings.append({"source": f"factType:{fid}", "target_identity": token, "identity_source": source, "kind": "fact_type", "status": "preserved"})
        local_used: set[str] = set()
        player_counts: dict[str, int] = {}
        for idx, role in enumerate(fact.get("roles") or []):
            rid = str(role["id"])
            oid = str(role["objectTypeId"])
            player_counts[oid] = player_counts.get(oid, 0) + 1
            raw_name = role.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                base = _lower_ident(object_name[oid], f"role{idx + 1}")
                if player_counts[oid] > 1:
                    base = f"{base}{player_counts[oid]}"
            else:
                base = _lower_ident(raw_name)
            role_name[rid] = _allocate(base, local_used, rid)
            token, source = _source_token(role, rid)
            role_token[rid] = token
            mappings.append({"source": f"role:{rid}", "target_identity": token, "identity_source": source, "kind": "role", "status": "preserved"})

    # Reference modes are genuine Factum identification semantics.  Represent them
    # as explicit Factgraph id fields unless the object is a subtype (which inherits
    # identity) or an objectification.
    synthetic_ref_value: dict[str, tuple[str, str]] = {}
    for oid, obj in object_rows.items():
        if obj.get("kind") != "entity" or obj.get("objectifiedFactTypeId"):
            continue
        ref_mode = obj.get("refMode")
        if not isinstance(ref_mode, str) or not ref_mode.strip():
            continue
        if oid in subtype_parents:
            issues.append({
                "path": f"objectTypes.{oid}.refMode",
                "status": "unsupported",
                "reason": "Factgraph subtypes inherit identification from their supertype; a separate subtype reference mode is not guessed",
            })
            continue
        dtype = str(obj.get("dataType") or _refmode_datatype(ref_mode))
        scalar = DATATYPES.get(dtype)
        if scalar is None:
            issues.append({"path": f"objectTypes.{oid}.dataType", "status": "unsupported", "reason": f"Factum dataType {dtype!r} has no bounded Factgraph scalar mapping"})
            scalar = "String"
        elif dtype in APPROXIMATE_DATATYPES:
            issues.append({"path": f"objectTypes.{oid}.dataType", "status": "approximated", "reason": APPROXIMATE_DATATYPES[dtype]})
        value_name = _allocate(f"{object_name[oid]}_{_ident(ref_mode)}_Reference", used_names, oid)
        value_identity = f"factum:refmode-value:{oid}"
        synthetic_ref_value[oid] = (value_name, value_identity)
        mappings.append({"source": f"objectType:{oid}.refMode", "target_identity": value_identity, "kind": "reference_value_type", "status": "preserved"})
        if obj.get("dataTypeLength") is not None or obj.get("dataTypeScale") is not None:
            issues.append({"path": f"objectTypes.{oid}.dataTypeLength/dataTypeScale", "status": "unsupported", "reason": "Factgraph scalar kinds do not currently carry lexical length/scale facets"})

    # Helper for role-sequence constraints.
    def resolve_sequence(role_ids: list[Any], *, path: str) -> tuple[str, list[str]] | None:
        if not role_ids:
            issues.append({"path": path, "status": "unsupported", "reason": "empty role sequence"})
            return None
        fids: set[str] = set()
        names: list[str] = []
        for raw_rid in role_ids:
            rid = str(raw_rid)
            if rid not in role_rows:
                raise ValueError(f"{path} references unknown role {rid!r}")
            fids.add(role_rows[rid][0])
            names.append(role_name[rid])
        if len(fids) != 1:
            issues.append({"path": path, "status": "unsupported", "reason": "Factgraph role sequences currently belong to one fact type; cross-fact join paths are not guessed"})
            return None
        return next(iter(fids)), names

    # Parse constraints into source fragments.  Deontic constraints are deliberately
    # skipped: importing them as alethic would strengthen the source model.
    constraints = [c for c in (raw.get("constraints") or []) if isinstance(c, dict)]
    for c in constraints:
        cid = str(c["id"])
        kind = str(c.get("kind") or "")
        path = f"constraints.{cid}"
        if c.get("modality") == "deontic":
            issues.append({"path": f"{path}.modality", "status": "unsupported", "reason": "Factgraph does not model deontic (should-not) versus alethic (cannot) constraints; deontic constraint is not strengthened into enforcement"})
            continue

        if kind in {"uniqueness", "mandatory", "frequency", "ring"}:
            roles = [str(x) for x in (c.get("roles") or [])]
            seq = resolve_sequence(roles, path=f"{path}.roles")
            if seq is None:
                continue
            fid, names = seq
            if kind == "uniqueness":
                local_fragments[fid].append(f"unique({', '.join(names)})")
                mappings.append({"source": path, "kind": "uniqueness_constraint", "target_construct": f"{fact_name[fid]}({', '.join(names)})", "status": "preserved"})
                if c.get("isPreferredIdentifier"):
                    issues.append({"path": f"{path}.isPreferredIdentifier", "status": "unsupported", "reason": "Factgraph preferred identifiers currently originate from entity value fields; arbitrary role-based preferred identification is not guessed. The underlying uniqueness is preserved."})
            elif kind == "mandatory":
                if len(names) == 1:
                    local_fragments[fid].append(f"mandatory({names[0]})")
                    mappings.append({"source": path, "kind": "mandatory_constraint", "target_construct": f"{fact_name[fid]}.{names[0]}", "status": "preserved"})
                else:
                    issues.append({"path": path, "status": "unsupported", "reason": "multi-role Factum mandatory is disjunctive; Factgraph mandatory is a single-role total-participation constraint"})
            elif kind == "frequency":
                lo = c.get("min")
                hi = c.get("max")
                if not isinstance(lo, int) or (hi is not None and not isinstance(hi, int)):
                    raise ValueError(f"Factum frequency constraint {cid!r} requires integer min and integer/null max")
                if hi is None:
                    issues.append({"path": path, "status": "unsupported", "reason": "unbounded Factum frequency maxima cannot be represented in the frozen Factgraph reference syntax without changing its language contract"})
                else:
                    local_fragments[fid].append(f"frequency({', '.join([*names, str(lo), str(hi)])})")
                    mappings.append({"source": path, "kind": "frequency_constraint", "target_construct": f"{fact_name[fid]}({', '.join(names)}) {lo}..{hi}", "status": "preserved"})
            else:  # ring
                types = [str(x) for x in (c.get("types") or [])]
                source_fact = fact_rows[fid]
                role_player_ids = [str(role_rows[r][1]["objectTypeId"]) for r in roles]
                can_symmetric = (
                    "symmetric" in types
                    and len(roles) == 2
                    and len(source_fact.get("roles") or []) == 2
                    and set(roles) == {str(r["id"]) for r in source_fact.get("roles") or []}
                    and len(set(role_player_ids)) == 1
                )
                if can_symmetric:
                    local_fragments[fid].append("symmetric")
                    mappings.append({"source": path, "kind": "ring:symmetric", "target_construct": fact_name[fid], "status": "preserved"})
                unsupported_types = [x for x in types if x != "symmetric"]
                if unsupported_types:
                    issues.append({"path": f"{path}.types", "status": "unsupported", "reason": f"Factgraph currently implements only logical symmetric ring semantics; unsupported: {', '.join(unsupported_types)}"})
                if "symmetric" in types and not can_symmetric:
                    issues.append({"path": f"{path}.types", "status": "unsupported", "reason": "Factum symmetric ring did not match Factgraph's binary same-player symmetric fact precondition"})

        elif kind in {"subset", "equality", "exclusion"}:
            sequences = c.get("roleSequences") or []
            if not isinstance(sequences, list) or len(sequences) != 2:
                issues.append({"path": f"{path}.roleSequences", "status": "unsupported", "reason": "bounded Factgraph set-comparison kernel currently represents exactly two role sequences"})
                continue
            left = resolve_sequence(list(sequences[0]), path=f"{path}.roleSequences[0]")
            right = resolve_sequence(list(sequences[1]), path=f"{path}.roleSequences[1]")
            if left is None or right is None:
                continue
            lfid, lnames = left
            rfid, rnames = right
            if len(lnames) != len(rnames):
                issues.append({"path": path, "status": "unsupported", "reason": "set-comparison role sequences have different arities"})
                continue
            keyword = kind
            set_fragments.append(f"{keyword} {fact_name[lfid]}({', '.join(lnames)}) {fact_name[rfid]}({', '.join(rnames)})")
            mappings.append({"source": path, "kind": f"{kind}_constraint", "target_construct": set_fragments[-1], "status": "preserved"})

        elif kind == "value":
            ranges = c.get("ranges") or []
            target_oid = c.get("objectTypeId")
            target_role = c.get("roleId")
            if target_role is not None:
                rid = str(target_role)
                if rid not in role_rows:
                    raise ValueError(f"Factum value constraint {cid!r} references unknown role {rid!r}")
                issues.append({"path": f"{path}.roleId", "status": "unsupported", "reason": "role-scoped value constraints cannot be safely lifted to a shared Factgraph ValueType without potentially strengthening other role usages"})
                continue
            if target_oid is None or str(target_oid) not in object_rows:
                raise ValueError(f"Factum value constraint {cid!r} references unknown objectTypeId")
            oid = str(target_oid)
            if object_rows[oid].get("kind") != "value" or object_rows[oid].get("objectifiedFactTypeId"):
                issues.append({"path": f"{path}.objectTypeId", "status": "unsupported", "reason": "bounded importer maps value constraints only when they target a lexical value type"})
                continue
            if not isinstance(ranges, list) or not ranges:
                raise ValueError(f"Factum value constraint {cid!r} has no ranges")
            if all(isinstance(r, dict) and "value" in r and "min" not in r and "max" not in r for r in ranges):
                vals = []
                for r in ranges:
                    v = r["value"]
                    if v not in vals:
                        vals.append(v)
                value_fragments.setdefault(oid, []).append(f"oneof({', '.join(_literal(v) for v in vals)})")
                mappings.append({"source": path, "kind": "value_enumeration", "target_construct": object_name[oid], "status": "preserved"})
            elif len(ranges) == 1 and isinstance(ranges[0], dict):
                r = ranges[0]
                if "min" in r and "max" in r and "value" not in r and r.get("minInclusive", True) is not False and r.get("maxInclusive", True) is not False:
                    value_fragments.setdefault(oid, []).append(f"range({_literal(r['min'])}, {_literal(r['max'])})")
                    mappings.append({"source": path, "kind": "value_range", "target_construct": object_name[oid], "status": "preserved"})
                else:
                    issues.append({"path": path, "status": "unsupported", "reason": "Factgraph value ranges currently require one bounded inclusive interval; exclusive or one-sided Factum ranges are not narrowed silently"})
            else:
                issues.append({"path": path, "status": "unsupported", "reason": "Factum ranges form a union; mixed/multiple intervals cannot be represented by Factgraph's current intersection-style value constraints without changing meaning"})

        elif kind == "cardinality":
            issues.append({"path": path, "status": "unsupported", "reason": "Factum object/role population cardinality is not the same contract as Factgraph fact-frequency or mandatory participation, so it is not coerced"})
        elif kind == "subtypeSet":
            issues.append({"path": path, "status": "unsupported", "reason": "exclusive/exhaustive subtype-set partitions are not in the current Factgraph subtype kernel"})
        else:
            issues.append({"path": path, "status": "unsupported", "reason": f"unknown/unsupported Factum constraint kind {kind!r}"})

    # Build the reference-language representation.
    lines: list[str] = [f"model {model_name} identity {_q(model_token)} {{"]

    # Source lexical value types.
    for oid, obj in object_rows.items():
        if obj.get("kind") != "value" or obj.get("objectifiedFactTypeId"):
            continue
        dtype = str(obj.get("dataType") or "string")
        scalar = DATATYPES.get(dtype)
        if scalar is None:
            scalar = "String"
            issues.append({"path": f"objectTypes.{oid}.dataType", "status": "unsupported", "reason": f"Factum dataType {dtype!r} has no bounded Factgraph scalar mapping; carried as opaque String"})
        elif dtype in APPROXIMATE_DATATYPES:
            issues.append({"path": f"objectTypes.{oid}.dataType", "status": "approximated", "reason": APPROXIMATE_DATATYPES[dtype]})
        fragments = value_fragments.get(oid, [])
        if fragments:
            lines.append(f"  value {object_name[oid]}: {scalar} identity {_q(object_token[oid])} {{")
            lines.extend(f"    {frag}" for frag in fragments)
            lines.append("  }")
        else:
            lines.append(f"  value {object_name[oid]}: {scalar} identity {_q(object_token[oid])}")
        if obj.get("dataTypeLength") is not None or obj.get("dataTypeScale") is not None:
            issues.append({"path": f"objectTypes.{oid}.dataTypeLength/dataTypeScale", "status": "unsupported", "reason": "Factgraph scalar kinds do not currently carry lexical length/scale facets"})
        if obj.get("population"):
            issues.append({"path": f"objectTypes.{oid}.population", "status": "metadata_only", "reason": "standalone object/value populations are not emitted as schema sample facts by this bounded importer"})

    # Synthetic reference-value types generated from Factum refMode declarations.
    for oid, (value_name, identity) in synthetic_ref_value.items():
        obj = object_rows[oid]
        ref_mode = str(obj.get("refMode") or "id")
        dtype = str(obj.get("dataType") or _refmode_datatype(ref_mode))
        scalar = DATATYPES.get(dtype, "String")
        lines.append(f"  value {value_name}: {scalar} identity {_q(identity)}")

    # Entity types except objectification proxies.
    for oid, obj in object_rows.items():
        if obj.get("kind") != "entity" or obj.get("objectifiedFactTypeId"):
            continue
        fields: list[str] = []
        if oid in synthetic_ref_value:
            value_name, _ = synthetic_ref_value[oid]
            ref_mode = _lower_ident(str(obj.get("refMode") or "id"), "id")
            fields.append(f"    id {ref_mode}: {value_name} identity {_q('factum:refmode-field:' + oid)}")
        lines.append(f"  entity {object_name[oid]} identity {_q(object_token[oid])} {{")
        lines.extend(fields)
        lines.append("  }")
        if obj.get("isIndependent"):
            issues.append({"path": f"objectTypes.{oid}.isIndependent", "status": "unsupported", "reason": "Factum's explicit independent-object marker is not represented as a Factgraph semantic constraint"})
        if obj.get("population"):
            issues.append({"path": f"objectTypes.{oid}.population", "status": "metadata_only", "reason": "standalone entity populations are not emitted as schema sample facts by this bounded importer"})

    # Single inheritance only; never pick one parent from a multiple-inheritance source.
    for sub, rels in sorted(subtype_parents.items()):
        if len(rels) != 1:
            for rel in rels:
                issues.append({"path": f"subtypeRelations.{rel['id']}", "status": "unsupported", "reason": "Factgraph currently allows one direct entity supertype; multiple Factum supertypes are not arbitrarily reduced"})
            continue
        rel = rels[0]
        sup = str(rel["supertypeId"])
        if object_rows[sub].get("kind") != "entity" or object_rows[sup].get("kind") != "entity" or object_rows[sub].get("objectifiedFactTypeId") or object_rows[sup].get("objectifiedFactTypeId"):
            issues.append({"path": f"subtypeRelations.{rel['id']}", "status": "unsupported", "reason": "Factgraph subtype semantics currently apply only to ordinary entity types"})
            continue
        lines.append(f"  subtype {object_name[sub]} is {object_name[sup]}")
        mappings.append({"source": f"subtypeRelation:{rel['id']}", "kind": "subtype", "target_construct": f"{object_name[sub]} is {object_name[sup]}", "status": "preserved"})
        if rel.get("isPreferredIdentificationPath"):
            issues.append({"path": f"subtypeRelations.{rel['id']}.isPreferredIdentificationPath", "status": "metadata_only", "reason": "with one direct supertype, Factgraph inherits identity through that path but does not separately label it preferred"})

    # Facts, one canonical reading each, local constraints and objectification.
    for fid, fact in fact_rows.items():
        role_parts: list[str] = []
        for role in fact.get("roles") or []:
            rid = str(role["id"])
            oid = str(role["objectTypeId"])
            role_parts.append(f"{role_name[rid]}: {object_name[oid]} identity {_q(role_token[rid])}")
        objectify = ""
        if fid in objectification_by_fact:
            oid = objectification_by_fact[fid]
            objectify = f" objectify {object_name[oid]} identity {_q(object_token[oid])}"
        lines.append(f"  fact {fact_name[fid]} identity {_q(fact_token[fid])}({', '.join(role_parts)}){objectify} {{")

        readings = [r for r in (fact.get("readings") or []) if isinstance(r, dict)]
        if readings:
            primary = next((r for r in readings if r.get("isPrimary")), readings[0])
            transformed, err = _reading_text(primary, role_name)
            if transformed is not None:
                lines.append(f"    reading {_q(transformed)}")
                mappings.append({"source": f"reading:{primary.get('id')}", "kind": "reading", "target_construct": fact_name[fid], "status": "preserved"})
            else:
                issues.append({"path": f"factTypes.{fid}.readings.{primary.get('id')}", "status": "unsupported", "reason": err or "reading could not be translated"})
            for extra in readings:
                if extra is primary:
                    continue
                issues.append({"path": f"factTypes.{fid}.readings.{extra.get('id')}", "status": "metadata_only", "reason": "Factgraph reference syntax currently persists one canonical reading per fact; alternate Factum reading remains in the source import report"})
        for frag in local_fragments[fid]:
            lines.append(f"    {frag}")
        lines.append("  }")

        if fact.get("isDerived") or fact.get("derivationRule"):
            issues.append({"path": f"factTypes.{fid}.derivationRule", "status": "unsupported", "reason": "Factgraph imports the relation's shape but does not model/evaluate Factum derivation rules; derived-versus-asserted status is therefore not preserved"})
        if fact.get("hints"):
            issues.append({"path": f"factTypes.{fid}.hints", "status": "metadata_only", "reason": "Factum target-generation hints do not change conceptual semantics and are not fed into Factgraph target adapters"})

    for frag in sorted(set_fragments):
        lines.append(f"  {frag}")

    # Sample fact populations are directly representable when all role values are known.
    for fid, fact in fact_rows.items():
        for idx, inst in enumerate(fact.get("population") or []):
            if not isinstance(inst, dict) or not isinstance(inst.get("values"), list):
                issues.append({"path": f"factTypes.{fid}.population[{idx}]", "status": "unsupported", "reason": "sample fact instance is not an object with values[]"})
                continue
            vals = inst["values"]
            if len(vals) != len(fact.get("roles") or []):
                issues.append({"path": f"factTypes.{fid}.population[{idx}]", "status": "unsupported", "reason": "sample tuple arity does not match fact role arity"})
                continue
            if any(v is None for v in vals):
                issues.append({"path": f"factTypes.{fid}.population[{idx}]", "status": "unsupported", "reason": "Factum null sample values mean unknown; Factgraph sample populations do not encode unknown values"})
                continue
            lines.append(f"  sample {fact_name[fid]}({', '.join(_literal(v) for v in vals)})")
            mappings.append({"source": f"factTypes.{fid}.population[{idx}]", "kind": "sample_fact", "target_construct": fact_name[fid], "status": "preserved"})

    lines.append("}")
    generated = "\n".join(lines) + "\n"
    model = normalize_model(parse_model(generated))

    # Model-level non-semantic material is summarized once instead of producing a
    # noisy issue for every layout coordinate or documentation field.
    if raw.get("diagram"):
        issues.append({"path": "diagram", "status": "metadata_only", "reason": "diagram geometry/layout is outside the semantic portability audit"})
    if raw.get("hints"):
        issues.append({"path": "hints", "status": "metadata_only", "reason": "Factum generation hints are physical projection guidance, not conceptual semantics"})
    if raw.get("meta") or raw.get("note") or raw.get("lang") or raw.get("generator"):
        issues.append({"path": "model.metadata", "status": "metadata_only", "reason": "descriptive/provenance/language metadata is retained in the original source handoff but not normalized into the current semantic kernel"})

    gap_statuses = {"unsupported", "lossy_dropped", "approximated"}
    gaps = [x for x in issues if x.get("status") in gap_statuses]
    report = {
        "format": "factgraph-factum-import-report-v1",
        "source_format": "factum-orm-json",
        "source_version": version,
        "source_name": source_name,
        "model": model.name,
        "model_identity_source": model_identity_source,
        "status": "imported_with_gaps" if gaps else "imported",
        "mappings": sorted(mappings, key=lambda x: (x.get("source", ""), x.get("kind", ""))),
        "issues": sorted(issues, key=lambda x: (x.get("path", ""), x.get("status", ""), x.get("reason", ""))),
        "unsupported_or_lossy_count": len(gaps),
        "metadata_only_count": sum(i.get("status") == "metadata_only" for i in issues),
        "contract": (
            "Bounded Factum ORM v1/v2 importer. Preserves source/guid identity, entity/value/objectified types, ref-mode identification, "
            "n-ary facts and roles, one canonical reading, single inheritance, uniqueness, single-role mandatory, finite frequency, "
            "logical symmetry, two-sequence subset/equality/exclusion, simple value enumerations/inclusive bounded ranges, and known sample facts. "
            "Deontic modality, arbitrary preferred identification, disjunctive mandatory, non-symmetric ring families, role-scoped value constraints, "
            "population cardinality, subtype-set partitions, derivations, and other non-isomorphic constructs are reported rather than guessed."
        ),
    }
    return ImportResult(model, report, print_model(model))
