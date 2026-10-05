from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

from .conformance import ConformanceCase, mongo_cases, postgres_cases, typedb_cases
from .model import ConstraintKind, Model
from .targets import mongo, postgres, typedb


@dataclass(frozen=True)
class MutationArtifact:
    """A deliberately weakened target artifact used to test the test oracle.

    Mutation testing is intentionally downstream of semantic compilation: the
    conceptual model and conformance witnesses remain unchanged while one target
    enforcement mechanism is removed. A sound witness should therefore flip at
    least one affected runtime case from pass to fail when executed live.
    """

    id: str
    target: str
    model: str
    feature: str
    source_element: str
    description: str
    artifact_format: str
    artifact: str | dict[str, Any]
    affected_case_ids: tuple[str, ...]

    @property
    def artifact_sha256(self) -> str:
        if isinstance(self.artifact, str):
            raw = self.artifact.encode("utf-8")
        else:
            raw = (json.dumps(self.artifact, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def manifest_dict(self) -> dict[str, Any]:
        return {
            "format": "factgraph-mutation-artifact-v1",
            "id": self.id,
            "target": self.target,
            "model": self.model,
            "feature": self.feature,
            "source_element": self.source_element,
            "description": self.description,
            "artifact_format": self.artifact_format,
            "artifact_sha256": self.artifact_sha256,
            "affected_case_ids": list(self.affected_case_ids),
            "oracle": "At least one affected live conformance case must fail under this mutated artifact while the unmutated baseline passes.",
        }


def _matching_runtime_case_ids(cases: list[ConformanceCase], feature: str, source_element: str) -> tuple[str, ...]:
    return tuple(
        c.id for c in cases
        if c.mode == "runtime" and c.feature == feature and source_element in c.source_elements
    )


def _remove_table_clause(sql: str, table: str, clause_predicate: Callable[[str], bool]) -> str:
    pattern = re.compile(rf"(CREATE TABLE {re.escape(table)} \(\n)(.*?)(\n\);)", re.DOTALL)
    match = pattern.search(sql)
    if not match:
        raise ValueError(f"could not locate PostgreSQL table {table!r} in emitted SQL")
    lines = match.group(2).splitlines()
    index = next((i for i, line in enumerate(lines) if clause_predicate(line.strip())), None)
    if index is None:
        raise ValueError(f"could not locate requested PostgreSQL clause in table {table!r}")
    del lines[index]
    # Canonical emitter uses one clause per line with commas except the final clause.
    # Re-normalize commas inside the table body rather than relying on which line was removed.
    cleaned = [line.rstrip().rstrip(",") for line in lines]
    cleaned = [line + ("," if i < len(cleaned) - 1 else "") for i, line in enumerate(cleaned)]
    replacement = match.group(1) + "\n".join(cleaned) + match.group(3)
    return sql[: match.start()] + replacement + sql[match.end() :]


def postgres_remove_fact_set_key(model: Model, fact_name: str | None = None) -> MutationArtifact:
    facts = sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.name)
    if not facts:
        raise ValueError("model has no source fact to mutate")
    fact = next((f for f in facts if f.name == fact_name), facts[0])
    table = next(t for t in postgres.build_plan(model).tables if t.conceptual_kind == "fact" and t.source_name == fact.name)
    sql = postgres.emit_sql(model)
    if table.primary_key:
        mutated = _remove_table_clause(sql, table.name, lambda line: line.startswith("PRIMARY KEY "))
    elif table.uniques:
        columns = ", ".join(table.uniques[0])
        mutated = _remove_table_clause(sql, table.name, lambda line: line == f"UNIQUE ({columns})")
    else:
        raise ValueError(f"fact {fact.name!r} has no tuple key to remove")
    case_ids = _matching_runtime_case_ids(postgres_cases(model), "fact_type", fact.id)
    if not case_ids:
        raise ValueError(f"no PostgreSQL live fact-set witness exists for {fact.name!r}")
    return MutationArtifact(
        id=f"postgres-drop-fact-set-key:{fact.id}",
        target="postgres",
        model=model.name,
        feature="fact_type",
        source_element=fact.id,
        description=f"Remove the tuple key from PostgreSQL fact table {table.name}; duplicate conceptual fact witness should become accepted.",
        artifact_format="sql",
        artifact=mutated,
        affected_case_ids=case_ids,
    )


def postgres_remove_first_value_check(model: Model) -> MutationArtifact:
    constraint = next((c for c in sorted(model.constraints.values(), key=lambda c: c.id) if c.kind == ConstraintKind.VALUE), None)
    if constraint is None:
        raise ValueError("model has no value constraint to mutate")
    plan = postgres.build_plan(model)
    table = next((t for t in plan.tables if t.checks), None)
    if table is None:
        raise ValueError("PostgreSQL projection contains no CHECK for the value constraint")
    check = table.checks[0]
    mutated = _remove_table_clause(postgres.emit_sql(model), table.name, lambda line: line == f"CHECK ({check})")
    case_ids = _matching_runtime_case_ids(postgres_cases(model), "value", constraint.id)
    if not case_ids:
        raise ValueError("no PostgreSQL live value witness exists")
    return MutationArtifact(
        id=f"postgres-drop-value-check:{constraint.id}",
        target="postgres",
        model=model.name,
        feature="value",
        source_element=constraint.id,
        description=f"Remove PostgreSQL CHECK ({check}) from {table.name}; the invalid value witness should become accepted.",
        artifact_format="sql",
        artifact=mutated,
        affected_case_ids=case_ids,
    )


def mongo_remove_fact_unique_index(model: Model, fact_name: str | None = None) -> MutationArtifact:
    facts = sorted((f for f in model.fact_types.values() if f.id not in model.field_hints), key=lambda f: f.name)
    if not facts:
        raise ValueError("model has no source fact to mutate")
    fact = next((f for f in facts if f.name == fact_name), facts[0])
    plan = mongo.build_plan(model)
    collection = next(c for c in plan["collections"] if c.get("conceptual_kind") == "fact" and c.get("source_name") == fact.name)
    spec = json.loads(mongo.emit_spec_json(model))
    target = next(c for c in spec["collections"] if c["name"] == collection["name"])
    unique_indexes = [i for i in target["indexes"] if i.get("unique")]
    if not unique_indexes:
        raise ValueError(f"fact collection {target['name']!r} has no unique index to remove")
    removed = unique_indexes[-1]
    target["indexes"] = [i for i in target["indexes"] if i is not removed]
    case_ids = _matching_runtime_case_ids(mongo_cases(model), "fact_type", fact.id)
    if not case_ids:
        raise ValueError(f"no MongoDB live fact-set witness exists for {fact.name!r}")
    return MutationArtifact(
        id=f"mongo-drop-fact-set-index:{fact.id}",
        target="mongo",
        model=model.name,
        feature="fact_type",
        source_element=fact.id,
        description=f"Remove the compound unique index from MongoDB fact collection {target['name']}; duplicate conceptual fact witness should become accepted.",
        artifact_format="json",
        artifact=spec,
        affected_case_ids=case_ids,
    )


def _remove_value_keywords(node: Any) -> bool:
    if isinstance(node, dict):
        removed_here = False
        for key in ("minimum", "maximum", "enum"):
            if key in node:
                del node[key]
                removed_here = True
        if removed_here:
            return True
        for value in node.values():
            if _remove_value_keywords(value):
                return True
    elif isinstance(node, list):
        for value in node:
            if _remove_value_keywords(value):
                return True
    return False


def mongo_remove_first_value_validator(model: Model) -> MutationArtifact:
    constraint = next((c for c in sorted(model.constraints.values(), key=lambda c: c.id) if c.kind == ConstraintKind.VALUE), None)
    if constraint is None:
        raise ValueError("model has no value constraint to mutate")
    spec = json.loads(mongo.emit_spec_json(model))
    mutated = deepcopy(spec)
    if not _remove_value_keywords(mutated):
        raise ValueError("MongoDB projection contains no JSON-safe value validator to remove")
    case_ids = _matching_runtime_case_ids(mongo_cases(model), "value", constraint.id)
    if not case_ids:
        raise ValueError("no MongoDB live value witness exists")
    return MutationArtifact(
        id=f"mongo-drop-value-validator:{constraint.id}",
        target="mongo",
        model=model.name,
        feature="value",
        source_element=constraint.id,
        description="Remove the first emitted MongoDB enum/minimum/maximum validator; the invalid value witness should become accepted.",
        artifact_format="json",
        artifact=mutated,
        affected_case_ids=case_ids,
    )


def typedb_remove_total_participation_cardinality(model: Model) -> MutationArtifact:
    candidates = [
        c for c in sorted(model.constraints.values(), key=lambda c: c.id)
        if c.kind == ConstraintKind.MANDATORY and c.fact_type_id is not None and c.fact_type_id not in model.field_hints
    ]
    if not candidates:
        raise ValueError("model has no generic mandatory participation constraint")
    constraint = candidates[0]
    fact = model.fact_types[constraint.fact_type_id]
    role = next(r for r in fact.roles if r.id == constraint.role_ids[0])
    relation_label = typedb.relation_label(model, fact.id)
    role_label = typedb.role_label(role.name)
    needle = f"plays {relation_label}:{role_label} @card(1..)"
    schema = typedb.emit_schema(model)
    if needle not in schema:
        raise ValueError(f"TypeDB projection does not contain expected total-participation mechanism {needle!r}")
    mutated = schema.replace(needle, f"plays {relation_label}:{role_label}", 1)
    case_ids = _matching_runtime_case_ids(typedb_cases(model), "mandatory", constraint.id)
    if not case_ids:
        raise ValueError("no isolatable TypeDB total-participation live witness exists")
    return MutationArtifact(
        id=f"typedb-drop-total-participation-card:{constraint.id}",
        target="typedb",
        model=model.name,
        feature="mandatory",
        source_element=constraint.id,
        description=f"Remove @card(1..) from TypeDB player declaration for {fact.name}.{role.name}; the missing-participation witness should become accepted.",
        artifact_format="typeql",
        artifact=mutated,
        affected_case_ids=case_ids,
    )


def typedb_remove_first_key(model: Model) -> MutationArtifact:
    constraint = next((c for c in sorted(model.constraints.values(), key=lambda c: c.id) if c.kind == ConstraintKind.PREFERRED_IDENTIFIER and len(c.field_fact_ids) == 1), None)
    if constraint is None:
        raise ValueError("model has no single-field preferred identifier")
    schema = typedb.emit_schema(model)
    if " @key" not in schema:
        raise ValueError("TypeDB projection contains no @key")
    mutated = schema.replace(" @key", "", 1)
    case_ids = _matching_runtime_case_ids(typedb_cases(model), "preferred_identifier", constraint.id)
    if not case_ids:
        raise ValueError("no TypeDB identifier live witness exists")
    return MutationArtifact(
        id=f"typedb-drop-key:{constraint.id}",
        target="typedb",
        model=model.name,
        feature="preferred_identifier",
        source_element=constraint.id,
        description="Remove the first TypeDB @key annotation; duplicate identifier witness should become accepted.",
        artifact_format="typeql",
        artifact=mutated,
        affected_case_ids=case_ids,
    )


def default_benchmark_mutations(models: dict[str, Model]) -> list[MutationArtifact]:
    """Small cross-target mutation corpus tied to the public portability benchmark."""
    return [
        postgres_remove_fact_set_key(models["nary_set_semantics"], "Ternary"),
        mongo_remove_fact_unique_index(models["nary_set_semantics"], "Ternary"),
        postgres_remove_first_value_check(models["value_range"]),
        mongo_remove_first_value_validator(models["value_range"]),
        typedb_remove_total_participation_cardinality(models["total_participation"]),
        typedb_remove_first_key(models["simple_identifier"]),
    ]


def _mutation_dir_name(mutation: MutationArtifact) -> str:
    digest = hashlib.sha256(mutation.id.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^a-z0-9]+", "-", f"{mutation.target}-{mutation.model}-{mutation.feature}".lower()).strip("-")
    return f"{safe}-{digest}"


def write_mutation_catalog(models: dict[str, Model], out_dir: Any) -> dict[str, Any]:
    """Write the deterministic public mutation corpus to files."""
    from pathlib import Path

    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    mutations = default_benchmark_mutations(models)
    entries: list[dict[str, Any]] = []
    for mutation in mutations:
        d = root / _mutation_dir_name(mutation)
        d.mkdir(parents=True, exist_ok=True)
        if mutation.artifact_format == "sql":
            artifact_name = "mutated.sql"
            (d / artifact_name).write_text(str(mutation.artifact), encoding="utf-8")
        elif mutation.artifact_format == "typeql":
            artifact_name = "mutated.tql"
            (d / artifact_name).write_text(str(mutation.artifact), encoding="utf-8")
        else:
            artifact_name = "mutated.json"
            (d / artifact_name).write_text(json.dumps(mutation.artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = mutation.manifest_dict()
        manifest["artifact_file"] = artifact_name
        (d / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        entries.append({"directory": d.name, **manifest})
    catalog = {
        "format": "factgraph-mutation-catalog-v1",
        "mutation_count": len(entries),
        "mutations": entries,
        "contract": "Mutation artifacts deliberately weaken one generated enforcement mechanism. Live CI must show the unmutated baseline passes and at least one named affected case fails under each mutation.",
    }
    (root / "catalog.json").write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog


def evaluate_live_mutation(mutation: MutationArtifact, baseline: dict[str, Any], mutated: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a live mutation experiment without treating unavailable services as success."""
    baseline_complete = baseline.get("status") == "completed"
    baseline_passed = baseline_complete and bool(baseline.get("passed"))
    mutated_complete = mutated.get("status") == "completed"
    by_id = {r.get("case_id"): r for r in mutated.get("results", [])}
    affected = [by_id[cid] for cid in mutation.affected_case_ids if cid in by_id]
    affected_missing = [cid for cid in mutation.affected_case_ids if cid not in by_id]
    detected = bool(affected) and not affected_missing and any(not bool(r.get("passed")) for r in affected)
    passed = baseline_passed and mutated_complete and detected
    if not baseline_complete:
        status = "not_observed"
        reason = "baseline target run did not complete"
    elif not baseline_passed:
        status = "invalid_baseline"
        reason = "unmutated baseline did not pass, so the mutation experiment has no valid oracle"
    elif not mutated_complete:
        status = "not_observed"
        reason = "mutated target run did not complete"
    elif affected_missing:
        status = "failed"
        reason = "one or more affected conformance cases were missing from the mutated run"
    elif not detected:
        status = "survived_mutation"
        reason = "all affected cases still passed after the claimed enforcement mechanism was removed"
    else:
        status = "mutation_detected"
        reason = "at least one named affected witness flipped to failure under the weakened target artifact"
    return {
        "format": "factgraph-live-mutation-result-v1",
        "mutation": mutation.manifest_dict(),
        "status": status,
        "passed": passed,
        "reason": reason,
        "baseline": {"status": baseline.get("status"), "passed": baseline.get("passed")},
        "mutated": {"status": mutated.get("status"), "passed": mutated.get("passed")},
        "affected_results": affected,
        "missing_affected_case_ids": affected_missing,
    }
