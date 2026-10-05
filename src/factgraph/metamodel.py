from __future__ import annotations

"""Versioned Factgraph metamodels and model-as-data codecs.

The claim in this module is deliberately narrow and executable:

* normalized Factgraph models can be represented as populations of a Factgraph
  metamodel;
* the metamodel itself is a Factgraph model loaded through the ordinary parser;
* supported metamodel versions can coexist; and
* a stored population can migrate between compatible metamodel versions by
  decoding through its source codec and re-encoding through the destination
  codec while checking that represented semantics survive.

This is *not* a Python bootstrap and it is not a claim of a universal
metamodel for arbitrary modeling languages.
"""

import hashlib
from importlib.resources import files
import json
from typing import Any

from .ids import stable_id
from .model import (
    Constraint,
    ConstraintKind,
    EntityType,
    FactType,
    FieldProjectionHint,
    Model,
    ObjectifiedFactType,
    Reading,
    Role,
    SampleFact,
    SourceNote,
    ValueType,
)
from .validate import validate_model


METAMODEL_FORMAT = "factgraph-metamodel-v2"
POPULATION_FORMAT = "factgraph-metamodel-population-v2"
LEGACY_POPULATION_FORMAT = "factgraph-metamodel-population-v1"
SUPPORTED_METAMODEL_VERSIONS = ("1", "2")
CURRENT_METAMODEL_VERSION = "2"


# Stable public names form the codec contract across metamodel versions.
MM = {
    "model": "MM_Model",
    "object": "MM_ObjectType",
    "fact": "MM_FactType",
    "role": "MM_Role",
    "reading": "MM_Reading",
    "constraint": "MM_Constraint",
    "analysis": "MM_Analysis",
    "sample": "MM_Sample",
    "field_hint": "MM_FieldHint",
    "source_note": "MM_SourceNote",
    "text": "MM_Text",
    "integer": "MM_Integer",
    "boolean": "MM_Boolean",
    "json": "MM_Json",
}


def _normalise_version(version: str | None) -> str:
    version = CURRENT_METAMODEL_VERSION if version is None else str(version)
    if version not in SUPPORTED_METAMODEL_VERSIONS:
        raise ValueError(
            f"unsupported Factgraph metamodel version {version!r}; "
            f"supported versions: {', '.join(SUPPORTED_METAMODEL_VERSIONS)}"
        )
    return version


def metamodel_source(version: str | None = None) -> str:
    """Return the packaged canonical Factgraph metamodel source for ``version``."""
    version = _normalise_version(version)
    resource = files("factgraph").joinpath(f"data/metamodel/v{version}.fg")
    return resource.read_text(encoding="utf-8")


def build_metamodel(version: str | None = None) -> Model:
    """Parse and normalize a versioned metamodel through the ordinary front end."""
    from .normalize import normalize_model
    from .parser import parse_model

    return normalize_model(parse_model(metamodel_source(version)))


def metamodel_versions() -> list[dict[str, Any]]:
    """Return deterministic metadata for every codec/metamodel version shipped."""
    out: list[dict[str, Any]] = []
    for version in SUPPORTED_METAMODEL_VERSIONS:
        model = build_metamodel(version)
        out.append(
            {
                "version": version,
                "current": version == CURRENT_METAMODEL_VERSION,
                "model_id": model.id,
                "model_name": model.name,
                "semantic_sha256": semantic_hash(model),
                "object_type_count": len(model.object_types),
                "fact_type_count": len(model.fact_types),
                "constraint_count": len(model.constraints),
            }
        )
    return out


def _clone_model(model: Model) -> Model:
    return Model.from_manifest_dict(model.manifest_dict())


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _instance_id(kind: str, *parts: str) -> str:
    return stable_id("metapop", kind, *parts)


def semantic_hash(model: Model) -> str:
    return hashlib.sha256(model.semantic_json(include_samples=False).encode("utf-8")).hexdigest()


def manifest_hash(model: Model) -> str:
    return hashlib.sha256(model.manifest_json().encode("utf-8")).hexdigest()


def _fact_exists(model: Model, fact_name: str) -> bool:
    return any(f.name == fact_name for f in model.fact_types.values())


def encode_model(
    subject: Model,
    *,
    include_envelope: bool = False,
    metamodel_version: str | None = None,
) -> Model:
    """Encode ``subject`` as a population of a selected metamodel version.

    Core mode represents :meth:`Model.semantic_dict`. Envelope mode additionally
    carries sample populations, field projection hints, and source provenance so
    that :meth:`Model.manifest_dict` can round-trip exactly.

    Metamodel v2 adds integrity facts describing the codec version and hashes.
    These rows describe the *encoding envelope*, not additional subject-domain
    semantics.
    """

    version = _normalise_version(metamodel_version)
    populated = _clone_model(build_metamodel(version))

    def add(fact_name: str, *values: object) -> None:
        fact = populated.fact_by_name(fact_name)
        populated.samples.append(SampleFact(fact.id, tuple(str(v) for v in values), None))

    model_token = subject.id
    add("MM_ModelName", model_token, subject.name)
    if version == "2":
        add("MM_ModelCodecVersion", model_token, version)
        add("MM_ModelSemanticHash", model_token, semantic_hash(subject))
        if include_envelope:
            add("MM_ModelManifestHash", model_token, manifest_hash(subject))

    for oid in sorted(subject.object_types):
        obj = subject.object_types[oid]
        add("MM_ModelObjectType", model_token, obj.id)
        add("MM_ObjectTypeName", obj.id, obj.name)
        if isinstance(obj, EntityType):
            add("MM_ObjectTypeKind", obj.id, "entity")
        elif isinstance(obj, ValueType):
            add("MM_ObjectTypeKind", obj.id, "value")
            add("MM_ValueTypeScalarKind", obj.id, obj.scalar_kind)
        else:
            add("MM_ObjectTypeKind", obj.id, "objectified_fact")
            add("MM_ObjectifiedFactTarget", obj.id, obj.fact_type_id)

    for fid in sorted(subject.fact_types):
        fact = subject.fact_types[fid]
        add("MM_ModelFactType", model_token, fact.id)
        add("MM_FactTypeName", fact.id, fact.name)
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            add("MM_FactRole", fact.id, role.id, role.ordinal)
            add("MM_RoleName", role.id, role.name)
            add("MM_RolePlayer", role.id, role.player_id)

    for rid in sorted(subject.readings):
        reading = subject.readings[rid]
        add("MM_ModelReading", model_token, reading.id)
        add("MM_ReadingFact", reading.id, reading.fact_type_id)
        add("MM_ReadingTemplate", reading.id, reading.template)
        for idx, role_id in enumerate(reading.role_ids):
            add("MM_ReadingRole", reading.id, role_id, idx)

    for cid in sorted(subject.constraints):
        c = subject.constraints[cid]
        add("MM_ModelConstraint", model_token, c.id)
        add("MM_ConstraintKind", c.id, c.kind.value)
        if c.fact_type_id is not None:
            add("MM_ConstraintFact", c.id, c.fact_type_id)
        for idx, role_id in enumerate(c.role_ids):
            add("MM_ConstraintRole", c.id, role_id, idx)
        if c.target_fact_type_id is not None:
            add("MM_ConstraintTargetFact", c.id, c.target_fact_type_id)
        for idx, role_id in enumerate(c.target_role_ids):
            add("MM_ConstraintTargetRole", c.id, role_id, idx)
        if c.object_type_id is not None:
            add("MM_ConstraintObjectType", c.id, c.object_type_id)
        for idx, fact_id in enumerate(c.field_fact_ids):
            add("MM_ConstraintFieldFact", c.id, fact_id, idx)
        if c.ring_kind is not None:
            add("MM_ConstraintRingKind", c.id, c.ring_kind)
        if c.min_frequency is not None:
            add("MM_ConstraintMinFrequency", c.id, c.min_frequency)
        if c.max_frequency is not None:
            add("MM_ConstraintMaxFrequency", c.id, c.max_frequency)
        if c.value_spec is not None:
            add("MM_ConstraintValueSpec", c.id, _json_text(c.value_spec))
        if c.subtype_id is not None:
            add("MM_ConstraintSubtype", c.id, c.subtype_id)
        if c.supertype_id is not None:
            add("MM_ConstraintSupertype", c.id, c.supertype_id)

    for idx, (name, args) in enumerate(sorted(subject.analyses)):
        token = _instance_id("analysis", subject.id, f"{idx:06d}", name, *args)
        add("MM_ModelAnalysis", model_token, token)
        add("MM_AnalysisName", token, name)
        add("MM_AnalysisArgs", token, _json_text(list(args)))

    if include_envelope:
        for idx, sample in enumerate(subject.samples):
            token = _instance_id("sample", subject.id, f"{idx:06d}")
            add("MM_ModelSample", model_token, token)
            add("MM_SampleFact", token, sample.fact_type_id)
            for ordinal, value in enumerate(sample.values):
                add("MM_SampleValue", token, value, ordinal)
            if sample.source_line is not None:
                add("MM_SampleSourceLine", token, sample.source_line)

        for idx, ffid in enumerate(sorted(subject.field_hints)):
            h = subject.field_hints[ffid]
            token = _instance_id("field_hint", subject.id, f"{idx:06d}", h.field_fact_id)
            add("MM_ModelFieldHint", model_token, token)
            add("MM_FieldHintOwner", token, h.owner_object_type_id)
            add("MM_FieldHintFact", token, h.field_fact_id)
            add("MM_FieldHintName", token, h.field_name)
            add("MM_FieldHintValueType", token, h.value_type_id)
            add("MM_FieldHintRequired", token, _bool_text(h.required))
            add("MM_FieldHintIdentifier", token, _bool_text(h.identifier_component))

        for idx, element_id in enumerate(sorted(subject.source_notes)):
            n = subject.source_notes[element_id]
            token = _instance_id("source_note", subject.id, f"{idx:06d}", n.element_id)
            add("MM_ModelSourceNote", model_token, token)
            add("MM_SourceNoteElement", token, n.element_id)
            add("MM_SourceNoteLine", token, n.line)

    return populated


def _rows(populated: Model) -> dict[str, list[tuple[str, ...]]]:
    by_id = {f.id: f.name for f in populated.fact_types.values()}
    result: dict[str, list[tuple[str, ...]]] = {}
    for sample in populated.samples:
        if sample.fact_type_id not in by_id:
            raise ValueError(f"population references unknown fact id {sample.fact_type_id!r}")
        result.setdefault(by_id[sample.fact_type_id], []).append(tuple(sample.values))
    for values in result.values():
        values.sort()
    return result


def detect_metamodel_version(populated: Model) -> str:
    """Determine the metamodel version from the population's base schema."""
    base = _clone_model(populated)
    base.samples = []
    matches = [
        version
        for version in SUPPORTED_METAMODEL_VERSIONS
        if base.semantically_equal(build_metamodel(version))
    ]
    if len(matches) != 1:
        raise ValueError(
            "population base schema does not uniquely match a supported Factgraph metamodel version"
        )
    return matches[0]


def _ensure_metamodel(populated: Model, version: str | None = None) -> str:
    detected = detect_metamodel_version(populated)
    if version is not None and detected != _normalise_version(version):
        raise ValueError(
            f"population uses metamodel v{detected}, not requested v{_normalise_version(version)}"
        )
    return detected


def _single_map(rows: dict[str, list[tuple[str, ...]]], fact_name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows.get(fact_name, []):
        key, value = row[0], row[1]
        if key in out and out[key] != value:
            raise ValueError(f"metamodel population has conflicting {fact_name} values for {key}")
        out[key] = value
    return out


def _ordered_map(rows: dict[str, list[tuple[str, ...]]], fact_name: str) -> dict[str, list[str]]:
    grouped: dict[str, list[tuple[int, str]]] = {}
    for row in rows.get(fact_name, []):
        grouped.setdefault(row[0], []).append((int(row[2]), row[1]))
    out: dict[str, list[str]] = {}
    for key, items in grouped.items():
        items.sort()
        ordinals = [i for i, _ in items]
        if ordinals != list(range(len(ordinals))):
            raise ValueError(
                f"metamodel population has non-contiguous ordinals in {fact_name} for {key}"
            )
        out[key] = [value for _, value in items]
    return out


def _parse_bool(text: str) -> bool:
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"invalid metamodel boolean literal {text!r}")


def decode_model(
    populated: Model,
    *,
    include_envelope: bool = False,
    metamodel_version: str | None = None,
) -> Model:
    """Decode a subject model from a versioned populated metamodel."""

    version = _ensure_metamodel(populated, metamodel_version)
    diagnostics = validate_model(populated)
    errors = [d for d in diagnostics if d.severity.value == "error"]
    if errors:
        raise ValueError(
            f"invalid metamodel population: {errors[0].code}: {errors[0].message}"
        )

    rows = _rows(populated)
    model_names = _single_map(rows, "MM_ModelName")
    if len(model_names) != 1:
        raise ValueError(f"expected exactly one modeled subject, found {len(model_names)}")
    model_id, model_name = next(iter(model_names.items()))

    if version == "2":
        codec_versions = _single_map(rows, "MM_ModelCodecVersion")
        if codec_versions.get(model_id) != version:
            raise ValueError(
                f"v2 population is missing or contradicts MM_ModelCodecVersion for {model_id}"
            )

    subject = Model(model_id, model_name)

    object_names = _single_map(rows, "MM_ObjectTypeName")
    object_kinds = _single_map(rows, "MM_ObjectTypeKind")
    scalar_kinds = _single_map(rows, "MM_ValueTypeScalarKind")
    objectified_targets = _single_map(rows, "MM_ObjectifiedFactTarget")
    object_ids = sorted(
        row[1] for row in rows.get("MM_ModelObjectType", []) if row[0] == model_id
    )
    for oid in object_ids:
        name = object_names[oid]
        kind = object_kinds[oid]
        if kind == "entity":
            obj = EntityType(oid, name)
        elif kind == "value":
            obj = ValueType(oid, name, scalar_kinds[oid])
        elif kind == "objectified_fact":
            obj = ObjectifiedFactType(oid, name, objectified_targets[oid])
        else:
            raise ValueError(f"unknown object type kind {kind!r}")
        subject.object_types[oid] = obj

    fact_names = _single_map(rows, "MM_FactTypeName")
    role_names = _single_map(rows, "MM_RoleName")
    role_players = _single_map(rows, "MM_RolePlayer")
    fact_role_rows: dict[str, list[tuple[int, str]]] = {}
    for fact_id, role_id, ordinal in rows.get("MM_FactRole", []):
        fact_role_rows.setdefault(fact_id, []).append((int(ordinal), role_id))
    fact_ids = sorted(
        row[1] for row in rows.get("MM_ModelFactType", []) if row[0] == model_id
    )
    for fid in fact_ids:
        ritems = sorted(fact_role_rows.get(fid, []))
        roles = tuple(
            Role(role_id, fid, role_names[role_id], role_players[role_id], ordinal)
            for ordinal, role_id in ritems
        )
        subject.fact_types[fid] = FactType(fid, fact_names[fid], roles)

    reading_fact = _single_map(rows, "MM_ReadingFact")
    reading_template = _single_map(rows, "MM_ReadingTemplate")
    reading_roles = _ordered_map(rows, "MM_ReadingRole")
    reading_ids = sorted(
        row[1] for row in rows.get("MM_ModelReading", []) if row[0] == model_id
    )
    for rid in reading_ids:
        subject.readings[rid] = Reading(
            rid,
            reading_fact[rid],
            reading_template[rid],
            tuple(reading_roles.get(rid, [])),
        )

    constraint_kind = _single_map(rows, "MM_ConstraintKind")
    constraint_fact = _single_map(rows, "MM_ConstraintFact")
    constraint_roles = _ordered_map(rows, "MM_ConstraintRole")
    constraint_target_fact = _single_map(rows, "MM_ConstraintTargetFact")
    constraint_target_roles = _ordered_map(rows, "MM_ConstraintTargetRole")
    constraint_object = _single_map(rows, "MM_ConstraintObjectType")
    constraint_fields = _ordered_map(rows, "MM_ConstraintFieldFact")
    ring_kind = _single_map(rows, "MM_ConstraintRingKind")
    min_freq = _single_map(rows, "MM_ConstraintMinFrequency")
    max_freq = _single_map(rows, "MM_ConstraintMaxFrequency")
    value_spec = _single_map(rows, "MM_ConstraintValueSpec")
    subtype = _single_map(rows, "MM_ConstraintSubtype")
    supertype = _single_map(rows, "MM_ConstraintSupertype")
    constraint_ids = sorted(
        row[1] for row in rows.get("MM_ModelConstraint", []) if row[0] == model_id
    )
    for cid in constraint_ids:
        subject.constraints[cid] = Constraint(
            id=cid,
            kind=ConstraintKind(constraint_kind[cid]),
            fact_type_id=constraint_fact.get(cid),
            role_ids=tuple(constraint_roles.get(cid, [])),
            target_fact_type_id=constraint_target_fact.get(cid),
            target_role_ids=tuple(constraint_target_roles.get(cid, [])),
            object_type_id=constraint_object.get(cid),
            field_fact_ids=tuple(constraint_fields.get(cid, [])),
            ring_kind=ring_kind.get(cid),
            min_frequency=int(min_freq[cid]) if cid in min_freq else None,
            max_frequency=int(max_freq[cid]) if cid in max_freq else None,
            value_spec=json.loads(value_spec[cid]) if cid in value_spec else None,
            subtype_id=subtype.get(cid),
            supertype_id=supertype.get(cid),
        )

    analysis_name = _single_map(rows, "MM_AnalysisName")
    analysis_args = _single_map(rows, "MM_AnalysisArgs")
    analysis_ids = sorted(
        row[1] for row in rows.get("MM_ModelAnalysis", []) if row[0] == model_id
    )
    for aid in analysis_ids:
        subject.analyses.append((analysis_name[aid], tuple(json.loads(analysis_args[aid]))))

    if include_envelope:
        sample_fact = _single_map(rows, "MM_SampleFact")
        sample_values = _ordered_map(rows, "MM_SampleValue")
        sample_lines = _single_map(rows, "MM_SampleSourceLine")
        sample_ids = sorted(
            row[1] for row in rows.get("MM_ModelSample", []) if row[0] == model_id
        )
        for sid in sample_ids:
            subject.samples.append(
                SampleFact(
                    sample_fact[sid],
                    tuple(sample_values.get(sid, [])),
                    int(sample_lines[sid]) if sid in sample_lines else None,
                )
            )

        hint_owner = _single_map(rows, "MM_FieldHintOwner")
        hint_fact = _single_map(rows, "MM_FieldHintFact")
        hint_name = _single_map(rows, "MM_FieldHintName")
        hint_value_type = _single_map(rows, "MM_FieldHintValueType")
        hint_required = _single_map(rows, "MM_FieldHintRequired")
        hint_identifier = _single_map(rows, "MM_FieldHintIdentifier")
        hint_ids = sorted(
            row[1] for row in rows.get("MM_ModelFieldHint", []) if row[0] == model_id
        )
        for hid in hint_ids:
            h = FieldProjectionHint(
                owner_object_type_id=hint_owner[hid],
                field_fact_id=hint_fact[hid],
                field_name=hint_name[hid],
                value_type_id=hint_value_type[hid],
                required=_parse_bool(hint_required[hid]),
                identifier_component=_parse_bool(hint_identifier[hid]),
            )
            subject.field_hints[h.field_fact_id] = h

        note_element = _single_map(rows, "MM_SourceNoteElement")
        note_line = _single_map(rows, "MM_SourceNoteLine")
        note_ids = sorted(
            row[1] for row in rows.get("MM_ModelSourceNote", []) if row[0] == model_id
        )
        for nid in note_ids:
            n = SourceNote(note_element[nid], int(note_line[nid]))
            subject.source_notes[n.element_id] = n

    if version == "2":
        semantic_hashes = _single_map(rows, "MM_ModelSemanticHash")
        encoded_semantic_hash = semantic_hashes.get(model_id)
        if encoded_semantic_hash != semantic_hash(subject):
            raise ValueError("v2 population semantic integrity hash does not match decoded model")
        if include_envelope:
            manifest_hashes = _single_map(rows, "MM_ModelManifestHash")
            encoded_manifest_hash = manifest_hashes.get(model_id)
            if encoded_manifest_hash != manifest_hash(subject):
                raise ValueError("v2 population manifest integrity hash does not match decoded model")

    return subject


def population_dict(populated: Model) -> dict[str, Any]:
    """Return a canonical, target-independent serialization of metamodel rows."""

    version = _ensure_metamodel(populated)
    rows = _rows(populated)
    mm = build_metamodel(version)
    return {
        "format": POPULATION_FORMAT,
        "metamodel_version": version,
        "metamodel_id": mm.id,
        "metamodel_semantic_sha256": semantic_hash(mm),
        "row_count": sum(len(v) for v in rows.values()),
        "facts": [
            {"fact": fact_name, "rows": [list(row) for row in rows[fact_name]]}
            for fact_name in sorted(rows)
        ],
    }


def population_json(populated: Model) -> str:
    return json.dumps(population_dict(populated), indent=2, sort_keys=True) + "\n"


def population_from_dict(data: dict[str, Any]) -> Model:
    """Load a canonical population JSON object into its versioned metamodel."""

    fmt = data.get("format")
    if fmt not in {POPULATION_FORMAT, LEGACY_POPULATION_FORMAT}:
        raise ValueError(f"unsupported metamodel population format {fmt!r}")
    version = str(data.get("metamodel_version") or "1")
    version = _normalise_version(version)
    populated = _clone_model(build_metamodel(version))
    by_name = {fact.name: fact for fact in populated.fact_types.values()}
    seen_names: set[str] = set()
    for block in data.get("facts", []):
        fact_name = str(block["fact"])
        if fact_name in seen_names:
            raise ValueError(f"duplicate population fact block {fact_name!r}")
        seen_names.add(fact_name)
        if fact_name not in by_name:
            raise ValueError(
                f"population for metamodel v{version} references unknown fact {fact_name!r}"
            )
        fact = by_name[fact_name]
        for raw_row in block.get("rows", []):
            row = tuple(str(v) for v in raw_row)
            if len(row) != len(fact.roles):
                raise ValueError(
                    f"population row for {fact_name} has arity {len(row)}, expected {len(fact.roles)}"
                )
            populated.samples.append(SampleFact(fact.id, row, None))
    expected_count = data.get("row_count")
    actual_count = len(populated.samples)
    if expected_count is not None and int(expected_count) != actual_count:
        raise ValueError(
            f"population row_count says {expected_count}, but {actual_count} rows were loaded"
        )
    if data.get("metamodel_id") not in {None, populated.id}:
        raise ValueError("population metamodel_id does not match selected metamodel")
    expected_hash = data.get("metamodel_semantic_sha256")
    if expected_hash is not None and expected_hash != semantic_hash(populated):
        raise ValueError("population metamodel semantic hash does not match packaged version")
    return populated


def population_from_json(text: str) -> Model:
    return population_from_dict(json.loads(text))


def migration_report(
    populated: Model,
    *,
    target_version: str,
    include_envelope: bool = True,
) -> tuple[Model, dict[str, Any]]:
    """Migrate a stored metamodel population by semantic decode/re-encode."""

    source_version = detect_metamodel_version(populated)
    target_version = _normalise_version(target_version)
    subject = decode_model(
        populated,
        include_envelope=include_envelope,
        metamodel_version=source_version,
    )
    migrated = encode_model(
        subject,
        include_envelope=include_envelope,
        metamodel_version=target_version,
    )
    recovered = decode_model(
        migrated,
        include_envelope=include_envelope,
        metamodel_version=target_version,
    )
    semantic_equal = subject.semantically_equal(recovered)
    manifest_equal = (
        subject.manifest_dict() == recovered.manifest_dict()
        if include_envelope
        else None
    )
    report = {
        "format": "factgraph-metamodel-population-migration-v1",
        "source_version": source_version,
        "target_version": target_version,
        "mode": "envelope" if include_envelope else "semantic_core",
        "source_row_count": population_dict(populated)["row_count"],
        "target_row_count": population_dict(migrated)["row_count"],
        "subject": {
            "id": subject.id,
            "name": subject.name,
            "semantic_sha256": semantic_hash(subject),
            "manifest_sha256": manifest_hash(subject) if include_envelope else None,
        },
        "semantic_roundtrip_equal": semantic_equal,
        "manifest_roundtrip_equal": manifest_equal,
        "migration_passed": bool(semantic_equal and (manifest_equal is not False)),
        "method": "decode with source metamodel codec, then encode with destination metamodel codec",
        "non_claims": [
            "Rows are not heuristically rewritten between metamodel versions.",
            "Compatibility is only claimed when the represented model survives the declared equality contract.",
        ],
    }
    return migrated, report


def metamodel_semantic_diff(from_version: str, to_version: str):
    """Compute the ordinary semantic diff between two packaged metamodel versions."""
    from .diff import MigrationHints, semantic_diff

    return semantic_diff(
        build_metamodel(_normalise_version(from_version)),
        build_metamodel(_normalise_version(to_version)),
        MigrationHints.empty(),
    )


def reification_report(
    subject: Model,
    *,
    metamodel_version: str | None = None,
) -> dict[str, Any]:
    version = _normalise_version(metamodel_version)
    core_population = encode_model(
        subject, include_envelope=False, metamodel_version=version
    )
    full_population = encode_model(
        subject, include_envelope=True, metamodel_version=version
    )
    core_decoded = decode_model(
        core_population, include_envelope=False, metamodel_version=version
    )
    full_decoded = decode_model(
        full_population, include_envelope=True, metamodel_version=version
    )
    core_errors = [
        d.code for d in validate_model(core_population) if d.severity.value == "error"
    ]
    full_errors = [
        d.code for d in validate_model(full_population) if d.severity.value == "error"
    ]
    mm = build_metamodel(version)
    return {
        "format": "factgraph-reification-report-v2",
        "subject": {
            "id": subject.id,
            "name": subject.name,
            "semantic_sha256": semantic_hash(subject),
        },
        "metamodel": {
            "version": version,
            "id": mm.id,
            "name": mm.name,
            "semantic_sha256": semantic_hash(mm),
        },
        "core_population": {
            "row_count": population_dict(core_population)["row_count"],
            "validation_errors": core_errors,
            "semantic_roundtrip_equal": subject.semantically_equal(core_decoded),
        },
        "envelope_population": {
            "row_count": population_dict(full_population)["row_count"],
            "validation_errors": full_errors,
            "semantic_roundtrip_equal": subject.semantically_equal(full_decoded),
            "manifest_roundtrip_equal": subject.manifest_dict()
            == full_decoded.manifest_dict(),
        },
    }


def self_host_report(version: str | None = None) -> dict[str, Any]:
    version = _normalise_version(version)
    metamodel = build_metamodel(version)
    population = encode_model(
        metamodel, include_envelope=True, metamodel_version=version
    )
    decoded = decode_model(
        population, include_envelope=True, metamodel_version=version
    )
    second_population = encode_model(
        decoded, include_envelope=True, metamodel_version=version
    )
    return {
        "format": "factgraph-self-host-report-v2",
        "claim": "A versioned Factgraph metamodel is a Factgraph model and can describe its own normalized semantic/manifest structure as a population.",
        "non_claims": [
            "The Python compiler implementation is not generated from this metamodel.",
            "This is not a proof that the metamodel is universal for arbitrary modeling languages.",
            "This does not make arbitrary database reverse engineering lossless.",
        ],
        "metamodel": {
            "version": version,
            "current": version == CURRENT_METAMODEL_VERSION,
            "id": metamodel.id,
            "name": metamodel.name,
            "object_type_count": len(metamodel.object_types),
            "fact_type_count": len(metamodel.fact_types),
            "constraint_count": len(metamodel.constraints),
            "semantic_sha256": semantic_hash(metamodel),
        },
        "self_population_row_count": population_dict(population)["row_count"],
        "population_validation_errors": [
            d.code for d in validate_model(population) if d.severity.value == "error"
        ],
        "semantic_roundtrip_equal": metamodel.semantically_equal(decoded),
        "manifest_roundtrip_equal": metamodel.manifest_dict()
        == decoded.manifest_dict(),
        "second_encoding_identical": population_dict(population)
        == population_dict(second_population),
    }
