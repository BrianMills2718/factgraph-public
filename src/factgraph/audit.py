from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from . import conformance
from .model import Constraint, ConstraintKind, EntityType, FactType, Model, ObjectifiedFactType, ValueType
from .reporting import CapabilityEntry, CapabilityStatus
from .targets import mongo, postgres, typedb
from .acceptance_witness import synthesize_acceptance_probes
from .ids import artifact_filename_token
from .witness import synthesize_counterexample
from .witness_lowering import lower_population, render_program


@dataclass(frozen=True)
class Obligation:
    id: str
    kind: str
    source_elements: tuple[str, ...]
    reading: str
    parameters: dict[str, Any]
    witness_recipe: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _obj_name(model: Model, object_id: str | None) -> str:
    obj = model.object_types.get(object_id or "")
    return obj.name if obj is not None else str(object_id)


def _role_names(model: Model, fact_id: str | None, role_ids: Iterable[str]) -> list[str]:
    fact = model.fact_types.get(fact_id or "")
    if fact is None:
        return list(role_ids)
    by_id = {r.id: r.name for r in fact.roles}
    return [by_id.get(rid, rid) for rid in role_ids]


def _fact_name(model: Model, fact_id: str | None) -> str:
    fact = model.fact_types.get(fact_id or "")
    return fact.name if fact is not None else str(fact_id)


def _constraint_obligation(model: Model, c: Constraint) -> Obligation:
    fact_name = _fact_name(model, c.fact_type_id)
    roles = _role_names(model, c.fact_type_id, c.role_ids)
    params: dict[str, Any] = {"constraint_kind": c.kind.value}
    recipe: tuple[str, ...]

    if c.kind == ConstraintKind.UNIQUENESS:
        reading = f"In {fact_name}, the role sequence ({', '.join(roles)}) is unique."
        recipe = (
            "Create one valid fact tuple.",
            f"Create a second tuple with the same ({', '.join(roles)}) values but a different remaining role value.",
            "The second tuple must be rejected if the uniqueness obligation is preserved.",
        )
        params["roles"] = roles
    elif c.kind == ConstraintKind.MANDATORY:
        role = roles[0] if roles else "role"
        fact = model.fact_types.get(c.fact_type_id or "")
        role_obj = next((r for r in fact.roles if r.id == c.role_ids[0]), None) if fact and c.role_ids else None
        player = _obj_name(model, role_obj.player_id if role_obj else None)
        if c.fact_type_id in model.field_hints:
            reading = f"Every {_obj_name(model, model.field_hints[c.fact_type_id].owner_object_type_id)} has a value for required field {model.field_hints[c.fact_type_id].field_name}."
            recipe = (
                "Create an owner instance without the required field value.",
                "The target must reject the instance if requiredness is preserved.",
            )
        else:
            reading = f"Every {player} participates in {fact_name} in role {role}."
            recipe = (
                f"Create a {player} instance.",
                f"Create no {fact_name} fact in which it plays {role}.",
                "A target that accepts this population does not enforce total participation.",
            )
        params.update({"fact": fact_name, "role": role, "player": player})
    elif c.kind == ConstraintKind.PREFERRED_IDENTIFIER:
        obj_name = _obj_name(model, c.object_type_id)
        fields = [model.field_hints[f].field_name for f in c.field_fact_ids if f in model.field_hints]
        reading = f"({', '.join(fields)}) is the preferred identifier of {obj_name}."
        recipe = (
            f"Create one {obj_name} with an identifier value.",
            f"Attempt to create a distinct {obj_name} with the same identifier value.",
            "The duplicate identifier must be rejected if identification is preserved.",
        )
        params.update({"object_type": obj_name, "fields": fields})
    elif c.kind == ConstraintKind.UNORDERED_ROLE_GROUP:
        reading = f"In {fact_name}, permutations of roles ({', '.join(roles)}) are semantically unordered."
        recipe = (
            "Choose distinct values for the unordered role positions.",
            "Attempt to represent both permutations as separate facts.",
            "A preserved mapping must prevent the two permutations from becoming distinct domain facts.",
        )
        params["roles"] = roles
    elif c.kind == ConstraintKind.RING:
        ring = c.ring_kind or "ring"
        if ring == "symmetric":
            reading = f"{fact_name} is symmetric: if R(a,b) holds, R(b,a) also holds."
            recipe = (
                "Insert R(a,b).",
                "Check whether R(b,a) is entailed or materialized according to the target mapping.",
                "If the target can retain only R(a,b) with no reverse fact, logical symmetry is not enforced.",
            )
        else:
            reading = f"{fact_name} has ring constraint {ring}."
            recipe = (f"Construct the smallest tuple pattern that violates ring property {ring}.", "Check whether the target accepts it.")
        params["ring_kind"] = ring
    elif c.kind == ConstraintKind.FREQUENCY:
        reading = f"Within existing {fact_name} facts, each ({', '.join(roles)}) value occurs {c.min_frequency}..{c.max_frequency} times."
        recipe = (
            f"Choose one ({', '.join(roles)}) key.",
            f"Create more than {c.max_frequency} {fact_name} facts sharing that key while varying other roles.",
            "The overflow fact must be rejected if the upper frequency bound is preserved.",
        )
        params.update({"roles": roles, "minimum": c.min_frequency, "maximum": c.max_frequency, "semantics_note": "This Factgraph frequency contract does not imply existence for unobserved role-player values."})
    elif c.kind == ConstraintKind.VALUE:
        obj_name = _obj_name(model, c.object_type_id)
        spec = c.value_spec or {}
        reading = f"Values of {obj_name} satisfy {json.dumps(spec, sort_keys=True)}."
        if spec.get("kind") == "range":
            recipe = (
                f"Use a {obj_name} value outside [{spec.get('min')}, {spec.get('max')}].",
                "The target must reject the containing fact/object if the value constraint is preserved.",
            )
        elif spec.get("kind") == "oneof":
            recipe = (
                f"Use a {obj_name} value not present in the allowed enumeration.",
                "The target must reject the containing fact/object if the value constraint is preserved.",
            )
        else:
            recipe = ("Construct a value violating the declared value specification.", "Check whether the target rejects it.")
        params.update({"value_type": obj_name, "spec": spec})
    elif c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}:
        target_fact = _fact_name(model, c.target_fact_type_id)
        target_roles = _role_names(model, c.target_fact_type_id, c.target_role_ids)
        left = f"{fact_name}({', '.join(roles)})"
        right = f"{target_fact}({', '.join(target_roles)})"
        if c.kind == ConstraintKind.SUBSET:
            reading = f"Projection {left} is a subset of {right}."
            recipe = (f"Create one projected tuple in {left}.", f"Do not create the corresponding tuple in {right}.", "Acceptance demonstrates that the subset rule is not enforced.")
        elif c.kind == ConstraintKind.EQUALITY:
            reading = f"Projections {left} and {right} are equal sets."
            recipe = (f"Create one projected tuple in only {left} (or only {right}).", "Acceptance demonstrates that equality of the projections is not enforced.")
        else:
            reading = f"Projections {left} and {right} are mutually exclusive."
            recipe = (f"Create the same projected tuple in both {left} and {right}.", "Acceptance demonstrates that exclusion is not enforced.")
        params.update({"left_fact": fact_name, "left_roles": roles, "right_fact": target_fact, "right_roles": target_roles})
    elif c.kind == ConstraintKind.SUBTYPE:
        sub = _obj_name(model, c.subtype_id)
        sup = _obj_name(model, c.supertype_id)
        reading = f"Every {sub} is also a {sup}."
        recipe = (f"Create a {sub} instance without corresponding {sup} membership/identity according to the target representation.", "Acceptance demonstrates that subtype population inclusion is not enforced.")
        params.update({"subtype": sub, "supertype": sup})
    else:
        reading = f"Constraint {c.id} ({c.kind.value}) must hold."
        recipe = ("Construct the smallest population violating this constraint.", "Check whether the target accepts it.")

    return Obligation(c.id, c.kind.value, (c.id,), reading, params, recipe)


def obligations(model: Model) -> list[Obligation]:
    out: list[Obligation] = []
    # Source facts have mathematical set semantics in Factgraph. Field-origin binary
    # facts are source sugar and get audited via their field/constraint obligations.
    for fact in sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda x: x.id):
        roles = [r.name for r in sorted(fact.roles, key=lambda r: r.ordinal)]
        reading_obj = next((r for r in sorted(model.readings.values(), key=lambda r: r.id) if r.fact_type_id == fact.id), None)
        reading = f"{fact.name} facts have set semantics: an identical ({', '.join(roles)}) tuple cannot occur twice."
        if reading_obj:
            reading += f" Reading: {reading_obj.template}"
        out.append(Obligation(
            id=f"obligation:set:{fact.id}",
            kind="fact_set_semantics",
            source_elements=(fact.id,),
            reading=reading,
            parameters={"fact": fact.name, "roles": roles},
            witness_recipe=(
                "Create one valid fact tuple.",
                "Attempt to create the identical tuple as a second domain fact.",
                "The duplicate tuple must not survive as a distinct fact if set semantics are preserved.",
            ),
        ))
    out.extend(_constraint_obligation(model, c) for c in sorted(model.constraints.values(), key=lambda c: c.id))
    return sorted(out, key=lambda o: (o.kind, o.id))


def _reports(model: Model) -> dict[str, Any]:
    return {
        "postgres": postgres.capability_report(model),
        "mongo": mongo.capability_report(model),
        "typedb": typedb.capability_report(model),
    }


def _cases(model: Model) -> dict[str, list[conformance.ConformanceCase]]:
    return {
        "postgres": conformance.postgres_cases(model),
        "mongo": conformance.mongo_cases(model),
        "typedb": conformance.typedb_cases(model),
    }


def _capability_for(report, source_elements: tuple[str, ...], obligation_kind: str) -> CapabilityEntry | None:
    candidates = [e for e in report.entries if e.source_element in source_elements]
    if obligation_kind == "fact_set_semantics":
        candidates = [e for e in candidates if e.feature == "fact_type"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda e: (e.feature, e.status.value))[0]


def _static_result_map(model: Model) -> dict[str, dict[str, Any]]:
    bundle = conformance.bundle(model)
    return {r["case_id"]: r for r in bundle["static"]["results"]}


def _live_result_map(live_report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not live_report or live_report.get("status") != "completed":
        return {}
    return {r.get("case_id"): r for r in live_report.get("results", []) if r.get("case_id")}


def _shared_result_map(shared_report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not shared_report or shared_report.get("status") != "completed":
        return {}
    return {r.get("obligation_id"): r for r in shared_report.get("results", []) if r.get("obligation_id")}


def _shared_preservation(status: CapabilityStatus | None, result: dict[str, Any] | None) -> str | None:
    """Return an authoritative semantic verdict only for fully asserted shared evidence."""
    if status is None or not result or result.get("status") != "observed":
        return None
    passed = result.get("passed")
    if passed is False:
        return "claim_falsified"
    if passed is not True:
        # In particular: a write was observed but a required post-state proof is pending.
        return None
    if status in {CapabilityStatus.NATIVE_ENFORCED, CapabilityStatus.EMULATED_ENFORCED}:
        return "preserved_observed"
    if status == CapabilityStatus.REPRESENTED_NOT_ENFORCED:
        return "weakened_observed"
    return None


def _preservation_from_status(
    status: CapabilityStatus | None,
    *,
    live_runtime_results: list[dict[str, Any]],
    live_status: str,
    live_complete: bool,
) -> str:
    """Map a capability status plus legacy live evidence to a preservation verdict.

    An *observed* verdict requires a passing live result for every matching
    runtime case (``live_complete``).  Partial coverage fails closed to the
    non-observed verdict; an observed failure still falsifies an enforcement
    claim, because a failure needs no coverage to be evidence.
    """
    if status is None:
        return "unknown"
    if status in {CapabilityStatus.UNSUPPORTED, CapabilityStatus.LOSSY_DROPPED}:
        return "lost"
    if status == CapabilityStatus.METADATA_ONLY:
        return "metadata_only"
    all_passed = bool(live_runtime_results) and all(r.get("passed") is True for r in live_runtime_results)
    if status == CapabilityStatus.REPRESENTED_NOT_ENFORCED:
        if live_complete and all_passed:
            return "weakened_observed"
        return "weakened"
    if status in {CapabilityStatus.NATIVE_ENFORCED, CapabilityStatus.EMULATED_ENFORCED}:
        if live_complete and all_passed:
            return "preserved_observed"
        if live_status == "completed" and live_runtime_results and not all_passed:
            return "claim_falsified"
        return "preserved_claimed"
    return "unknown"


def build_audit(
    model: Model, *, targets: Iterable[str] = ("postgres", "mongo"),
    live_reports: dict[str, dict[str, Any]] | None = None,
    shared_live_reports: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dangling = model.dangling_references()
    if dangling:
        # The obligation text would otherwise fall back to printing raw ids for
        # elements that do not exist and report verdicts on nothing.
        raise ValueError(
            "cannot audit a model with dangling references: "
            + "; ".join(f"{r.element_id}: {r.message}" for r in dangling)
        )
    selected = tuple(dict.fromkeys(targets))
    known_reports = _reports(model)
    known_cases = _cases(model)
    static_map = _static_result_map(model)
    live_reports = live_reports or {}
    shared_live_reports = shared_live_reports or {}
    obs = obligations(model)

    obligation_rows: list[dict[str, Any]] = []
    target_summary: dict[str, dict[str, int]] = {}
    for ob in obs:
        verdicts: dict[str, Any] = {}
        for target in selected:
            report = known_reports.get(target)
            cases = known_cases.get(target, [])
            live_report = live_reports.get(target, {"target": target, "status": "not_run", "reason": "no legacy live report supplied"})
            live_map = _live_result_map(live_report)
            shared_report = shared_live_reports.get(target, {"target": target, "status": "not_run", "reason": "no shared-witness live report supplied"})
            shared_map = _shared_result_map(shared_report)
            if report is None:
                verdicts[target] = {
                    "target": target,
                    "status": "unsupported_target_adapter",
                    "preservation": "unknown",
                    "evidence_level": "none",
                    "mapping": None,
                    "cases": [],
                    "live_execution": {"status": live_report.get("status", "not_run"), "reason": live_report.get("reason")},
                    "semantic_live_execution": {"status": shared_report.get("status", "not_run"), "reason": shared_report.get("reason")},
                    "evidence_source": "none",
                }
                continue
            cap = _capability_for(report, ob.source_elements, ob.kind)
            matching = [c for c in cases if set(c.source_elements).intersection(ob.source_elements)]
            if ob.kind == "fact_set_semantics":
                matching = [c for c in matching if c.feature == "fact_type"]
            structural = []
            runtime = []
            for case in matching:
                row = {"case_id": case.id, "mode": case.mode, "feature": case.feature, "description": case.description}
                if case.mode == "structural":
                    s = static_map.get(case.id)
                    row["result"] = s
                    structural.append(row)
                else:
                    row["generated"] = True
                    row["live_result"] = live_map.get(case.id)
                    runtime.append(row)
            live_runtime = [r["live_result"] for r in runtime if r.get("live_result") is not None]
            missing_live = [r["case_id"] for r in runtime if r.get("live_result") is None]
            live_complete = bool(runtime) and not missing_live
            status = cap.status if cap is not None else None
            shared_result = shared_map.get(ob.id)
            shared_verdict = _shared_preservation(status, shared_result)
            legacy_verdict = _preservation_from_status(
                status,
                live_runtime_results=live_runtime,
                live_status=live_report.get("status", "not_run"),
                live_complete=live_complete,
            )
            if shared_verdict is not None:
                preservation = shared_verdict
                evidence_level = "live_observed"
                evidence_source = "shared_semantic_witness"
            elif live_runtime and (live_complete or legacy_verdict == "claim_falsified"):
                preservation = legacy_verdict
                evidence_level = "live_observed"
                evidence_source = "legacy_target_witness"
            elif runtime:
                preservation = legacy_verdict
                evidence_level = "witness_tested"
                evidence_source = "generated_target_witness"
            elif structural:
                preservation = legacy_verdict
                evidence_level = "structurally_checked"
                evidence_source = "structural"
            elif cap is not None:
                preservation = legacy_verdict
                evidence_level = "declared"
                evidence_source = "capability_declaration"
            else:
                preservation = legacy_verdict
                evidence_level = "none"
                evidence_source = "none"
            coverage: dict[str, Any] | None = None
            if live_runtime:
                coverage = {
                    "runtime_cases": len(runtime),
                    "observed_runtime_cases": len(live_runtime),
                    "missing_case_ids": missing_live,
                    "complete": live_complete,
                    "reason": (
                        "every matching runtime case has a live result"
                        if live_complete
                        else f"{len(missing_live)} of {len(runtime)} matching runtime case(s) have no live result; "
                        "not promoted to an observed verdict"
                    ),
                }
            verdicts[target] = {
                "target": target,
                "status": status.value if status else "missing_capability_entry",
                "preservation": preservation,
                "evidence_level": evidence_level,
                "evidence_source": evidence_source,
                "mapping": {
                    "feature": cap.feature,
                    "mechanism": cap.mechanism,
                    "reason": cap.reason,
                } if cap else None,
                "cases": [*structural, *runtime],
                "live_execution": {
                    "status": live_report.get("status", "not_run"),
                    "server": live_report.get("server"),
                    "target_passed": live_report.get("passed"),
                    "reason": live_report.get("reason"),
                },
                "semantic_live_execution": {
                    "status": shared_report.get("status", "not_run"),
                    "server": shared_report.get("server"),
                    "target_passed": shared_report.get("passed"),
                    "reason": shared_report.get("reason"),
                    "result": shared_result,
                },
            }
            if coverage is not None:
                verdicts[target]["live_coverage"] = coverage
        obligation_rows.append({**ob.to_dict(), "verdicts": verdicts})

    for target in selected:
        vals = [row["verdicts"][target] for row in obligation_rows]
        target_summary[target] = {
            "obligations": len(vals),
            "preserved_observed": sum(v["preservation"] == "preserved_observed" for v in vals),
            "preserved_claimed": sum(v["preservation"] == "preserved_claimed" for v in vals),
            "weakened_observed": sum(v["preservation"] == "weakened_observed" for v in vals),
            "weakened": sum(v["preservation"] == "weakened" for v in vals),
            "lost": sum(v["preservation"] == "lost" for v in vals),
            "metadata_only": sum(v["preservation"] == "metadata_only" for v in vals),
            "claim_falsified": sum(v["preservation"] == "claim_falsified" for v in vals),
            "unknown": sum(v["preservation"] == "unknown" for v in vals),
            "live_observed": sum(v["evidence_level"] == "live_observed" for v in vals),
            "shared_semantic_live_observed": sum(v.get("evidence_source") == "shared_semantic_witness" for v in vals),
        }

    return {
        "format": "factgraph-semantic-portability-audit-v1",
        "model": model.name,
        "model_id": model.id,
        "targets": list(selected),
        "evidence_model": {
            "levels": ["declared", "structurally_checked", "witness_tested", "live_observed"],
            "rule": "Shared source-semantic witness execution is the authoritative live evidence channel. Static capability claims are never promoted without a fully asserted matching live observation; legacy target-specific witnesses remain secondary evidence.",
        },
        "summary": {"obligation_count": len(obligation_rows), "targets": target_summary},
        "obligations": obligation_rows,
    }


def audit_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Semantic Preservation Report — {report['model']}",
        "",
        "> This report distinguishes declared mappings, structural checks, generated executable witnesses, and live-observed target behavior. Shared source-semantic witnesses are the authoritative live evidence channel for preservation verdicts; legacy target-specific conformance remains secondary evidence.",
        "",
    ]
    source_import = report.get("source_import")
    if source_import:
        lines.extend([
            "## Source import",
            "",
            f"- Source format: `{source_import.get('source_format', 'unknown')}`",
            f"- Import status: **{source_import.get('status', 'unknown')}**",
            f"- Unsupported/lossy/approximated source constructs: **{source_import.get('unsupported_or_lossy_count', 0)}**",
            f"- Input semantic coverage: **{'complete for the bounded importer' if source_import.get('semantic_input_complete') else 'gapped — target verdicts apply only to the normalized subset'}**",
            "",
        ])
    lines.extend([
        "## Summary",
        "",
        "| Target | Obligations | Preserved (live) | Preserved (claimed) | Weakened (live) | Weakened | Lost | Metadata only | Falsified | Shared-semantic live | All live-observed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for target, s in report["summary"]["targets"].items():
        lines.append(f"| {target} | {s['obligations']} | {s['preserved_observed']} | {s['preserved_claimed']} | {s['weakened_observed']} | {s['weakened']} | {s['lost']} | {s['metadata_only']} | {s['claim_falsified']} | {s['shared_semantic_live_observed']} | {s['live_observed']} |")
    ce = report.get("semantic_counterexamples")
    if ce:
        counts = ce.get("counts", {})
        lines.extend([
            "",
            "## Source semantic counterexamples",
            "",
            f"- Isolated: **{counts.get('isolated', 0)}**",
            f"- Collateral/interacting constraints: **{counts.get('collateral', 0)}**",
            f"- Not independently falsifiable: **{counts.get('not_independently_falsifiable', 0)}**",
            f"- Unsupported by bounded synthesizer: **{counts.get('unsupported', 0)}**",
            f"- Locally irreducible isolated witnesses: **{ce.get('locally_irreducible_count', 0)}**",
            "",
            "These populations are checked by the target-independent semantic oracle before target execution. Local irreducibility is tested by deterministic single-element deletion and is not a claim of global cardinality minimality. They are distinct from backend-specific witness SQL/JSON/TypeQL.",
        ])
    acceptance = report.get("semantic_acceptance_probes")
    if acceptance:
        lines.extend([
            "",
            "## Source-valid acceptance probes",
            "",
            f"- Generated probes: **{acceptance.get('probe_count', 0)}**",
            f"- Obligations with probes: **{acceptance.get('obligation_count', 0)}**",
            "",
            "These are source-valid boundary populations used to detect target strengthening. v1 currently generates them only for value-domain constraints. Acceptance of all generated probes is still only probe-scoped evidence, not a proof of exact source/target equivalence.",
        ])
    lowering = report.get("shared_witness_lowering")
    if lowering:
        lines.extend(["", "## Shared-witness lowering", "", "The files in `witnesses/lowerings/` are derived from the exact source semantic counterexample population. A non-lowered status is kept as evidence instead of silently substituting a target-specific witness.", "", "| Target | Exact lowering | Representation prevents exact realization | Unsupported / needs context or identity |", "| --- | ---: | ---: | ---: |"]) 
        for target, counts in lowering.get("counts_by_target", {}).items():
            lines.append(f"| {target} | {counts.get('lowered', 0)} | {counts.get('representation_prevents_exact_realization', 0)} | {counts.get('unsupported', 0)} |")
    shared_live = report.get("shared_witness_live")
    if shared_live:
        lines.extend(["", "## Shared-witness live execution", "", "| Target | Status | Asserted cases | Post-state pending | Overall write-oracle result |", "| --- | --- | ---: | ---: | --- |"])
        for target, row in shared_live.get("targets", {}).items():
            lines.append(f"| {target} | `{row.get('status')}` | {row.get('asserted_case_count', 0)} | {row.get('poststate_pending_count', 0)} | {row.get('passed')} |")
        lines.extend(["", "Write acceptance for absence/entailment obligations is kept at `poststate_pending` until the target state is queried; it is not promoted to full semantic preservation/weakening evidence."])
    lines.extend(["", "## Obligations", ""])
    for ob in report["obligations"]:
        lines.extend([f"### `{ob['id']}`", "", f"**Rule:** {ob['reading']}"])
        source_ce = ob.get("source_counterexample")
        if source_ce:
            lines.extend(["", f"**Source counterexample:** `{source_ce['status']}` — {source_ce['note']}"])
        source_accept = ob.get("source_acceptance_probes") or []
        if source_accept:
            lines.extend(["", "**Source-valid acceptance probes:** " + ", ".join(f"`{p['label']}`" for p in source_accept)])
        shared = ob.get("shared_target_lowerings") or {}
        if shared:
            lines.extend(["", "**Exact source-population lowering:** " + ", ".join(f"{target}=`{row.get('status')}`" for target, row in shared.items())])
        shared_observed = ob.get("shared_witness_live") or {}
        if shared_observed:
            lines.extend(["", "**Shared-witness live:** " + ", ".join(f"{target}=`{row.get('semantic_evidence_level')}`" for target, row in shared_observed.items())])
        lines.extend(["", "**Witness recipe:"])
        lines.extend([f"- {step}" for step in ob["witness_recipe"]])
        lines.extend(["", "| Target | Mapping status | Preservation | Evidence | Mechanism / reason |", "| --- | --- | --- | --- | --- |"])
        for target, v in ob["verdicts"].items():
            mapping = v.get("mapping") or {}
            detail = mapping.get("mechanism") or mapping.get("reason") or "—"
            cell = str(detail).replace("|", "\\|")
            lines.append(f"| {target} | `{v['status']}` | **{v['preservation']}** | `{v['evidence_level']}` | {cell} |")
        for target, v in ob["verdicts"].items():
            if not v.get("cases"):
                continue
            lines.extend(["", f"**{target} evidence cases:**"])
            for case in v["cases"]:
                suffix = ""
                lr = case.get("live_result")
                if lr is not None:
                    suffix = f" — live: {'PASS' if lr.get('passed') else 'FAIL'}"
                elif case.get("mode") == "runtime":
                    suffix = " — generated, not observed live"
                elif case.get("result"):
                    suffix = f" — structural: {'PASS' if case['result'].get('passed') else 'FAIL'}"
                lines.append(f"- `{case['case_id']}`: {case['description']}{suffix}")
        lines.append("")
    lines.extend([
        "## Interpretation rule",
        "",
        "A `preserved_claimed` result is not a live proof. It means the adapter declares enforcement and has generated/structural evidence, but no matching live execution was supplied to this audit. `preserved_observed` requires a completed target run whose matching evidence case passed.",
        "",
    ])
    return "\n".join(lines)


def write_audit_bundle(
    model: Model,
    out_dir: Path,
    *,
    targets: Iterable[str] = ("postgres", "mongo"),
    live_reports: dict[str, dict[str, Any]] | None = None,
    source_import_report: dict[str, Any] | None = None,
    shared_live_reports: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = build_audit(model, targets=targets, live_reports=live_reports, shared_live_reports=shared_live_reports)
    if source_import_report is not None:
        gap_count = int(source_import_report.get("unsupported_or_lossy_count", 0) or 0)
        report["source_import"] = {
            "source_format": source_import_report.get("source_format"),
            "source_version": source_import_report.get("source_version"),
            "status": source_import_report.get("status"),
            "unsupported_or_lossy_count": gap_count,
            "semantic_input_complete": gap_count == 0,
            "rule": "Target preservation verdicts apply to the normalized semantic subset. Source-import gaps are reported separately and are never treated as preserved target semantics.",
        }

    if shared_live_reports is not None:
        shared_summary: dict[str, Any] = {}
        shared_maps: dict[str, dict[str, Any]] = {}
        for target, live in shared_live_reports.items():
            results = live.get("results", [])
            shared_maps[target] = {r.get("obligation_id"): r for r in results if r.get("obligation_id")}
            shared_summary[target] = {
                "status": live.get("status", "not_run"),
                "passed": live.get("passed"),
                "case_count": live.get("case_count", len(results)),
                "asserted_case_count": live.get("asserted_case_count", 0),
                "poststate_pending_count": sum(r.get("semantic_evidence_level") == "write_realization_observed_poststate_pending" for r in results),
                "reason": live.get("reason"),
            }
        report["shared_witness_live"] = {
            "format": "factgraph-shared-witness-live-summary-v1",
            "targets": shared_summary,
            "rule": "Shared-witness live execution is a stricter evidence channel derived from the exact source population. It does not promote write-only acceptance to full semantic proof when a post-state absence/entailment check is still required.",
        }
        for ob in report["obligations"]:
            ob["shared_witness_live"] = {target: rows.get(ob["id"]) for target, rows in shared_maps.items() if rows.get(ob["id"]) is not None}

    counterexamples: dict[str, dict[str, Any]] = {}
    counterexample_results: dict[str, Any] = {}
    counterexample_counts = {"isolated": 0, "collateral": 0, "not_independently_falsifiable": 0, "unsupported": 0}
    locally_irreducible_count = 0
    for ob in report["obligations"]:
        result = synthesize_counterexample(model, ob["id"], ob["kind"])
        payload = result.to_dict(model)
        counterexamples[ob["id"]] = payload
        counterexample_results[ob["id"]] = result
        counterexample_counts[result.status] = counterexample_counts.get(result.status, 0) + 1
        locally_irreducible_count += int(result.locally_irreducible)
        ob["source_counterexample"] = {
            "status": result.status,
            "target_violation_observed": result.target_violation_observed,
            "note": result.note,
            "minimality": payload["minimality"],
        }
    report["semantic_counterexamples"] = {
        "format": "factgraph-semantic-counterexample-summary-v1",
        "counts": counterexample_counts,
        "locally_irreducible_count": locally_irreducible_count,
        "all_isolated_counterexamples_locally_irreducible": locally_irreducible_count == counterexample_counts.get("isolated", 0),
        "minimality_claim": "Local irreducibility means no single fact-row occurrence or typed population member can be deleted while preserving an isolated target violation; no global minimum claim is made.",
        "oracle": "factgraph-semantic-population-v1",
        "rule": "An isolated counterexample is rejected by the source semantic oracle for exactly the intended obligation. Collateral means interacting constraints also reject the population; redundant/unsupported cases are not promoted to isolated evidence.",
    }

    acceptance_payloads: dict[str, list[dict[str, Any]]] = {}
    acceptance_probe_count = 0
    acceptance_obligation_count = 0
    for ob in report["obligations"]:
        probes = synthesize_acceptance_probes(model, ob["id"], ob["kind"])
        payloads = [p.to_dict(model) for p in probes]
        acceptance_payloads[ob["id"]] = payloads
        acceptance_probe_count += len(payloads)
        if payloads:
            acceptance_obligation_count += 1
        ob["source_acceptance_probes"] = [
            {"probe_id": p["probe_id"], "label": p["label"], "source_valid": p["source_valid"]}
            for p in payloads
        ]
    report["semantic_acceptance_probes"] = {
        "format": "factgraph-semantic-acceptance-probe-summary-v2",
        "probe_count": acceptance_probe_count,
        "obligation_count": acceptance_obligation_count,
        "supported_obligation_kinds": ["value"],
        "rule": "Acceptance probes are finite source-valid populations chosen to exercise permitted cases and selected boundaries. Rejection by a target is evidence of strengthening or incompatibility for that tested probe; acceptance is not a proof of full semantic equivalence.",
    }

    # Lower the *same* semantic counterexample population into every selected
    # target. This is distinct from historical target-specific conformance cases:
    # no alternate backend witness is substituted when exact lowering is not
    # possible. Gaps remain first-class evidence.
    shared_lowerings: dict[tuple[str, str], Any] = {}
    lowering_counts: dict[str, dict[str, int]] = {
        target: {"lowered": 0, "representation_prevents_exact_realization": 0, "unsupported": 0}
        for target in report["targets"]
    }
    for ob in report["obligations"]:
        result = counterexample_results[ob["id"]]
        ob_lowerings: dict[str, Any] = {}
        for target in report["targets"]:
            program = lower_population(model, result.population, target)
            shared_lowerings[(ob["id"], target)] = program
            lowering_counts[target][program.status] = lowering_counts[target].get(program.status, 0) + 1
            ob_lowerings[target] = {"status": program.status, "fidelity": program.fidelity, "limitations": list(program.limitations)}
        ob["shared_target_lowerings"] = ob_lowerings
    report["shared_witness_lowering"] = {
        "format": "factgraph-shared-witness-lowering-summary-v1",
        "source_population_format": "factgraph-semantic-population-v1",
        "counts_by_target": lowering_counts,
        "rule": "A lowered program is derived from the exact source semantic population. If a target shape collapses source atoms or Factgraph lacks required occurrence identity/context, the audit records that instead of substituting a different backend witness.",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "audit.md").write_text(audit_markdown(report), encoding="utf-8")
    (out_dir / "obligations.json").write_text(json.dumps(report["obligations"], indent=2, sort_keys=True) + "\n", encoding="utf-8")

    acceptance_dir = out_dir / "acceptance_probes"
    acceptance_dir.mkdir(parents=True, exist_ok=True)
    acceptance_index: list[dict[str, Any]] = []
    for ob in report["obligations"]:
        for payload in acceptance_payloads.get(ob["id"], []):
            safe_id = artifact_filename_token(payload["probe_id"])
            rel = f"acceptance_probes/{safe_id}.json"
            (out_dir / rel).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            acceptance_index.append({
                "obligation_id": ob["id"],
                "kind": ob["kind"],
                "probe_id": payload["probe_id"],
                "label": payload["label"],
                "file": rel,
            })
    (acceptance_dir / "index.json").write_text(json.dumps(acceptance_index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    witness_index = []
    for ob in report["obligations"]:
        safe_id = artifact_filename_token(ob["id"])
        rel = f"witnesses/{safe_id}.json"
        lowering_refs: dict[str, Any] = {}
        lowering_dir = out_dir / "witnesses" / "lowerings" / safe_id
        lowering_dir.mkdir(parents=True, exist_ok=True)
        for target in report["targets"]:
            program = shared_lowerings[(ob["id"], target)]
            meta_rel = f"witnesses/lowerings/{safe_id}/{target}.json"
            native_ext = {"postgres": "sql", "mongo": "operations.json", "typedb": "tql"}.get(target, "txt")
            native_rel = f"witnesses/lowerings/{safe_id}/{target}.{native_ext}"
            (out_dir / meta_rel).write_text(json.dumps(program.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (out_dir / native_rel).write_text(render_program(program), encoding="utf-8")
            lowering_refs[target] = {
                "status": program.status,
                "fidelity": program.fidelity,
                "metadata_file": meta_rel,
                "native_program_file": native_rel,
                "operation_count": len(program.operations),
                "limitations": list(program.limitations),
            }
        payload = {
            "format": "factgraph-semantic-witness-recipe-v3",
            "obligation_id": ob["id"],
            "kind": ob["kind"],
            "reading": ob["reading"],
            "recipe": ob["witness_recipe"],
            "source_counterexample": counterexamples[ob["id"]],
            "shared_target_lowerings": lowering_refs,
            "target_realizations": {
                target: [
                    {
                        "case_id": c["case_id"],
                        "mode": c["mode"],
                        "description": c["description"],
                    }
                    for c in verdict["cases"]
                ]
                for target, verdict in ob["verdicts"].items()
            },
        }
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        witness_index.append({"obligation_id": ob["id"], "file": rel, "source_counterexample_status": counterexamples[ob["id"]]["status"], "shared_lowerings": {target: shared_lowerings[(ob["id"], target)].status for target in report["targets"]}})
    (out_dir / "witnesses" / "index.json").write_text(json.dumps(witness_index, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    trace = {
        "format": "factgraph-transformation-trace-v1",
        "model": model.name,
        "rows": [
            {
                "obligation_id": ob["id"],
                "source_elements": ob["source_elements"],
                "targets": {
                    target: {
                        "status": verdict["status"],
                        "mapping": verdict["mapping"],
                        "case_ids": [c["case_id"] for c in verdict["cases"]],
                    }
                    for target, verdict in ob["verdicts"].items()
                },
            }
            for ob in report["obligations"]
        ],
    }
    (out_dir / "transformation_trace.json").write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Target-scoped handoff: verdicts stay separate from live execution so a
    # consumer cannot accidentally treat a static mapping claim as an observed run.
    for target in report["targets"]:
        target_dir = out_dir / "targets" / target
        target_dir.mkdir(parents=True, exist_ok=True)
        verdicts = [
            {
                "obligation_id": ob["id"],
                "kind": ob["kind"],
                **ob["verdicts"][target],
            }
            for ob in report["obligations"]
        ]
        (target_dir / "verdicts.json").write_text(json.dumps(verdicts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        live = (live_reports or {}).get(target, {
            "target": target,
            "status": "not_run",
            "reason": "no live target execution supplied to this audit",
        })
        (target_dir / "live.json").write_text(json.dumps(live, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        semantic_live = (shared_live_reports or {}).get(target, {
            "target": target,
            "status": "not_run",
            "reason": "no shared semantic-witness target execution supplied to this audit",
        })
        (target_dir / "semantic_live.json").write_text(json.dumps(semantic_live, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    summary = {
        "format": "factgraph-semantic-portability-audit-summary-v1",
        "model": model.name,
        **report["summary"],
        **({"source_import": report["source_import"]} if "source_import" in report else {}),
        "semantic_counterexamples": report["semantic_counterexamples"],
        "semantic_acceptance_probes": report["semantic_acceptance_probes"],
        "live_targets": {
            t: (live_reports or {}).get(t, {}).get("status", "not_run")
            for t in report["targets"]
        },
        "semantic_live_targets": {
            t: (shared_live_reports or {}).get(t, {}).get("status", "not_run")
            for t in report["targets"]
        },
    }
    (out_dir / "AUDIT_SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
