from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Any

from .model import Model
from .parser import parse_model
from .normalize import normalize_model
from .printer import print_model
from .validate import validate_model
from .reporting import Severity


_MISSING = object()


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _same(a: Any, b: Any) -> bool:
    if a is _MISSING or b is _MISSING:
        return a is b
    return a == b


def _snapshot(value: Any) -> Any:
    return None if value is _MISSING else value


def _conflict_id(kind: str, element_id: str, base: Any, ours: Any, theirs: Any) -> str:
    raw = _canon([kind, element_id, _snapshot(base), _snapshot(ours), _snapshot(theirs)]).encode("utf-8")
    return f"merge:{kind}:{hashlib.sha256(raw).hexdigest()[:20]}"


def _changed_fields(base: Any, side: Any) -> list[str]:
    if not isinstance(base, dict) or not isinstance(side, dict):
        return [] if _same(base, side) else ["value"]
    return sorted(k for k in set(base) | set(side) if base.get(k, _MISSING) != side.get(k, _MISSING))


def _label(value: Any, fallback: str) -> str:
    if isinstance(value, dict):
        for key in ("name", "field_name", "kind", "id"):
            if value.get(key):
                return str(value[key])
    return fallback


def _explain_conflict(kind: str, element_id: str, base: Any, ours: Any, theirs: Any, reason: str) -> dict[str, Any]:
    terms = {
        "object_type": ("object type", "population semantics", "An object type defines what may play roles. A conflicting change can alter identity, scalar/value semantics, or subtype meaning."),
        "fact_type": ("fact type", "population semantics", "A fact type is an n-ary predicate. A conflicting change can alter the conceptual relationship represented by one fact population."),
        "role": ("role", "population semantics", "A role is one typed argument position in a fact type. Changing its player, name, or position can change the meaning of every fact tuple."),
        "constraint": ("constraint", "population validity", "A constraint determines which fact populations are valid. Choosing one side can accept data the other side rejects."),
        "reading": ("fact reading", "verbalization", "A reading explains the predicate in human terms. This conflict is primarily about the model's verbalization unless role order also changed elsewhere."),
        "field_hint": ("field projection", "target projection", "A field hint is ergonomic/projection metadata derived from fact semantics. Conflicts can change how a conceptual fact is presented or emitted."),
        "sample": ("sample fact", "example population", "A sample fact is validation/example data rather than the schema itself, but conflicting examples can signal divergent assumptions about valid populations."),
        "analysis": ("analysis rule", "model analysis", "An analysis rule changes what structural property the model asks the compiler to evaluate."),
        "model_name": ("model name", "presentation", "Both branches renamed the same semantic model differently; its stable semantic identity is unchanged."),
        "model_identity": ("model identity", "identity", "The three inputs do not identify the same conceptual model. Merging them would require an explicit identity decision before element-level reconciliation."),
    }
    orm_term, impact, detail = terms.get(kind, (kind.replace("_", " "), "semantic element", "Both branches changed the same stable semantic element differently."))
    base_fields = _changed_fields(base, ours)
    theirs_fields = _changed_fields(base, theirs)
    overlapping = sorted(set(base_fields) & set(theirs_fields))
    label = _label(ours if ours is not _MISSING else theirs, element_id)
    suggestions = [
        "Choose `ours` only if the ours branch expresses the intended conceptual meaning.",
        "Choose `theirs` only if the theirs branch expresses the intended conceptual meaning.",
    ]
    if base is not _MISSING:
        suggestions.append("Choose `base` to discard both branch edits to this element.")
    suggestions.append("Choose `delete` only if removing this semantic element is intentional and the rebuilt model still validates.")
    return {
        "orm_term": orm_term,
        "label": label,
        "semantic_impact": impact,
        "why_it_matters": detail,
        "reason": reason,
        "ours_changed_fields": base_fields,
        "theirs_changed_fields": theirs_fields,
        "overlapping_changed_fields": overlapping,
        "decision_question": f"Which meaning of {orm_term} {label!r} should the merged model assert?",
        "suggested_checks": suggestions,
    }


@dataclass(frozen=True)
class MergeConflict:
    id: str
    kind: str
    element_id: str
    reason: str
    base: Any
    ours: Any
    theirs: Any
    explanation: dict[str, Any] | None = None
    allowed_resolutions: tuple[str, ...] = ("ours", "theirs", "base", "delete")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MergeResult:
    base_model: str
    ours_model: str
    theirs_model: str
    conflicts: list[MergeConflict]
    applied_resolutions: dict[str, str]
    merged_model: Model | None
    validation_errors: list[dict[str, Any]]

    @property
    def status(self) -> str:
        if self.conflicts:
            return "conflicts"
        if self.validation_errors:
            return "invalid_merge"
        return "merged"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "factgraph-semantic-merge-v2",
            "status": self.status,
            "base_model": self.base_model,
            "ours_model": self.ours_model,
            "theirs_model": self.theirs_model,
            "summary": {
                "conflict_count": len(self.conflicts),
                "applied_resolution_count": len(self.applied_resolutions),
                "validation_error_count": len(self.validation_errors),
                "merged": self.merged_model is not None and not self.conflicts and not self.validation_errors,
            },
            "conflicts": [c.to_dict() for c in self.conflicts],
            "applied_resolutions": dict(sorted(self.applied_resolutions.items())),
            "validation_errors": self.validation_errors,
            "merged_semantic_sha256": (
                hashlib.sha256(self.merged_model.semantic_json(False).encode("utf-8")).hexdigest()
                if self.merged_model is not None
                else None
            ),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def merged_source(self) -> str | None:
        return print_model(self.merged_model) if self.merged_model is not None else None

    def to_markdown(self) -> str:
        lines = [
            "# Semantic merge explanation",
            "",
            f"Status: **{self.status}**",
            "",
            f"Base model: `{self.base_model}`  ",
            f"Ours: `{self.ours_model}`  ",
            f"Theirs: `{self.theirs_model}`",
            "",
        ]
        if self.conflicts:
            lines += [
                "## Conflicts",
                "",
                "Factgraph does not resolve these automatically because each conflict changes one stable semantic element in incompatible ways.",
                "",
            ]
            for conflict in self.conflicts:
                e = conflict.explanation or {}
                lines += [
                    f"### {e.get('orm_term', conflict.kind).title()}: {e.get('label', conflict.element_id)}",
                    "",
                    f"- Conflict ID: `{conflict.id}`",
                    f"- Semantic identity: `{conflict.element_id}`",
                    f"- Impact: **{e.get('semantic_impact', 'semantic')}**",
                    f"- Why it matters: {e.get('why_it_matters', conflict.reason)}",
                    f"- Ours changed: {', '.join(e.get('ours_changed_fields', [])) or 'element presence/value'}",
                    f"- Theirs changed: {', '.join(e.get('theirs_changed_fields', [])) or 'element presence/value'}",
                    f"- Overlap: {', '.join(e.get('overlapping_changed_fields', [])) or 'none identified'}",
                    f"- Decision: {e.get('decision_question', conflict.reason)}",
                    f"- Allowed resolutions: {', '.join(conflict.allowed_resolutions)}",
                    "",
                ]
        elif self.validation_errors:
            lines += ["## Validation errors", ""]
            for err in self.validation_errors:
                lines.append(f"- `{err.get('code')}`: {err.get('message')}")
            lines.append("")
        else:
            lines += [
                "## Result",
                "",
                "The three-way semantic merge completed without unresolved element conflicts.",
                f"Applied explicit resolutions: **{len(self.applied_resolutions)}**.",
                "",
            ]
        return "\n".join(lines).rstrip() + "\n"


def _model_parts(model: Model) -> dict[str, dict[str, Any]]:
    semantic = model.semantic_dict(include_samples=False)
    objects = {x["id"]: x for x in semantic["object_types"]}
    facts: dict[str, dict[str, Any]] = {}
    roles: dict[str, dict[str, Any]] = {}
    for f in semantic["fact_types"]:
        facts[f["id"]] = {"id": f["id"], "name": f["name"]}
        for r in f["roles"]:
            roles[r["id"]] = r
    readings = {x["id"]: x for x in semantic["readings"]}
    constraints = {x["id"]: x for x in semantic["constraints"]}
    field_hints = {x["field_fact_id"]: x for x in model.manifest_dict().get("field_hints", [])}
    samples = {
        _canon([s.fact_type_id, list(s.values)]): {
            "fact_type_id": s.fact_type_id,
            "values": list(s.values),
            "source_line": None,
        }
        for s in model.samples
    }
    analyses = {
        _canon([name, list(args)]): [name, list(args)]
        for name, args in model.analyses
    }
    return {
        "object_type": objects,
        "fact_type": facts,
        "role": roles,
        "reading": readings,
        "constraint": constraints,
        "field_hint": field_hints,
        "sample": samples,
        "analysis": analyses,
    }


def _choose_resolution(choice: str, base: Any, ours: Any, theirs: Any) -> Any:
    if choice == "ours":
        return ours
    if choice == "theirs":
        return theirs
    if choice == "base":
        return base
    if choice == "delete":
        return _MISSING
    raise ValueError(f"unsupported merge resolution {choice!r}")


def _merge_value(
    kind: str,
    element_id: str,
    base: Any,
    ours: Any,
    theirs: Any,
    resolutions: dict[str, str],
    conflicts: list[MergeConflict],
    applied: dict[str, str],
) -> Any:
    if _same(ours, theirs):
        return ours
    if _same(ours, base):
        return theirs
    if _same(theirs, base):
        return ours

    if base is _MISSING:
        reason = "both branches added the same semantic identity differently"
    elif ours is _MISSING or theirs is _MISSING:
        reason = "one branch deleted an element that the other branch modified"
    else:
        reason = "both branches modified the same semantic identity differently"
    cid = _conflict_id(kind, element_id, base, ours, theirs)
    choice = resolutions.get(cid)
    if choice is not None:
        chosen = _choose_resolution(choice, base, ours, theirs)
        applied[cid] = choice
        return chosen
    conflicts.append(
        MergeConflict(
            cid,
            kind,
            element_id,
            reason,
            _snapshot(base),
            _snapshot(ours),
            _snapshot(theirs),
            _explain_conflict(kind, element_id, base, ours, theirs, reason),
        )
    )
    return _MISSING


def _merge_map(
    kind: str,
    base: dict[str, Any],
    ours: dict[str, Any],
    theirs: dict[str, Any],
    resolutions: dict[str, str],
    conflicts: list[MergeConflict],
    applied: dict[str, str],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for element_id in sorted(set(base) | set(ours) | set(theirs)):
        merged = _merge_value(
            kind,
            element_id,
            base.get(element_id, _MISSING),
            ours.get(element_id, _MISSING),
            theirs.get(element_id, _MISSING),
            resolutions,
            conflicts,
            applied,
        )
        if merged is not _MISSING:
            out[element_id] = merged
    return out


def _rebuild_manifest(
    model_id: str,
    model_name: str,
    parts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    roles_by_fact: dict[str, list[dict[str, Any]]] = {}
    for role in parts["role"].values():
        roles_by_fact.setdefault(role["fact_type_id"], []).append(role)
    fact_types = []
    for fid, header in sorted(parts["fact_type"].items()):
        roles = sorted(roles_by_fact.get(fid, []), key=lambda r: (r["ordinal"], r["id"]))
        fact_types.append({"id": fid, "name": header["name"], "roles": roles})

    return {
        "id": model_id,
        "name": model_name,
        "object_types": [parts["object_type"][k] for k in sorted(parts["object_type"])],
        "fact_types": fact_types,
        "readings": [parts["reading"][k] for k in sorted(parts["reading"])],
        "constraints": [parts["constraint"][k] for k in sorted(parts["constraint"])],
        "analyses": [parts["analysis"][k] for k in sorted(parts["analysis"])],
        "samples": [parts["sample"][k] for k in sorted(parts["sample"])],
        "field_hints": [parts["field_hint"][k] for k in sorted(parts["field_hint"])],
        "source_notes": [],
    }


def semantic_merge(
    base: Model,
    ours: Model,
    theirs: Model,
    resolutions: dict[str, str] | None = None,
) -> MergeResult:
    """Three-way merge normalized semantic models by stable element identity."""

    resolutions = {str(k): str(v) for k, v in (resolutions or {}).items()}
    conflicts: list[MergeConflict] = []
    applied: dict[str, str] = {}

    if not (base.id == ours.id == theirs.id):
        cid = _conflict_id("model_identity", "model", base.id, ours.id, theirs.id)
        reason = "the three models do not share one semantic model identity"
        conflict = MergeConflict(
            cid,
            "model_identity",
            "model",
            reason,
            base.id,
            ours.id,
            theirs.id,
            _explain_conflict("model_identity", "model", base.id, ours.id, theirs.id, reason),
            ("ours", "theirs", "base"),
        )
        return MergeResult(base.name, ours.name, theirs.name, [conflict], {}, None, [])

    name = _merge_value(
        "model_name",
        base.id,
        base.name,
        ours.name,
        theirs.name,
        resolutions,
        conflicts,
        applied,
    )
    if name is _MISSING:
        name = ours.name

    bp, op, tp = _model_parts(base), _model_parts(ours), _model_parts(theirs)
    merged_parts: dict[str, dict[str, Any]] = {}
    for kind in ["object_type", "fact_type", "role", "reading", "constraint", "field_hint", "sample", "analysis"]:
        merged_parts[kind] = _merge_map(
            kind,
            bp[kind],
            op[kind],
            tp[kind],
            resolutions,
            conflicts,
            applied,
        )

    if conflicts:
        return MergeResult(base.name, ours.name, theirs.name, conflicts, applied, None, [])

    manifest = _rebuild_manifest(base.id, str(name), merged_parts)
    validation_errors: list[dict[str, Any]] = []
    merged_model: Model | None = None
    try:
        provisional = Model.from_manifest_dict(manifest)
        # Reparse the canonical source to regenerate projection hints/source
        # provenance consistently and prove the merged semantic model is
        # expressible by the ordinary source language.
        canonical = print_model(provisional)
        merged_model = normalize_model(parse_model(canonical))
        diagnostics = validate_model(merged_model)
        validation_errors = [
            {"code": d.code, "message": d.message, "element_id": d.element_id}
            for d in diagnostics
            if d.severity == Severity.ERROR
        ]
        if validation_errors:
            merged_model = None
    except Exception as exc:
        validation_errors = [{"code": "merge_rebuild_failed", "message": str(exc), "element_id": None}]
        merged_model = None

    return MergeResult(base.name, ours.name, theirs.name, [], applied, merged_model, validation_errors)
