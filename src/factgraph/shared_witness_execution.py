from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Any

from .audit import Obligation, obligations
from .execution_witness import prepare_execution_witness
from .model import ConstraintKind, EntityType, Model, ValueType
from .reporting import CapabilityStatus
from .targets import mongo, postgres, typedb
from .witness import synthesize_counterexample
from .witness_lowering import LoweringProgram, lower_population
from . import witness_lowering as lowering


_ENFORCED = {CapabilityStatus.NATIVE_ENFORCED, CapabilityStatus.EMULATED_ENFORCED}
_WEAK = {CapabilityStatus.REPRESENTED_NOT_ENFORCED}
_ABSENCE_OR_ENTAILMENT_KINDS = {"mandatory", "subset", "equality", "ring", "subtype"}


@dataclass(frozen=True)
class SharedWitnessCase:
    obligation_id: str
    kind: str
    target: str
    source_counterexample_status: str
    execution_witness_mode: str
    execution_context_added: bool
    execution_context_note: str
    execution_population: dict[str, Any]
    lowering_status: str
    lowering_fidelity: str
    expected_write_outcome: str  # prevented | realized | not_asserted | not_executable
    poststate_required_for_semantic_proof: bool
    capability_status: str | None
    capability_mechanism: str | None
    capability_reason: str | None
    postconditions: tuple[dict[str, Any], ...]
    program: LoweringProgram

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["program"] = self.program.to_dict()
        return out


def _target_report(model: Model, target: str):
    if target == "postgres":
        return postgres.capability_report(model)
    if target == "mongo":
        return mongo.capability_report(model)
    if target == "typedb":
        return typedb.capability_report(model)
    raise ValueError(f"unknown target {target}")


def _capability(model: Model, target: str, ob: Obligation):
    report = _target_report(model, target)
    source = ob.source_elements[0] if ob.source_elements else None
    candidates = [e for e in report.entries if e.source_element in ob.source_elements]
    if ob.kind == "fact_set_semantics":
        candidates = [e for e in candidates if e.feature == "fact_type"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda e: (e.feature, e.status.value))[0]



def _absence_pattern(model: Model, pop, ob: Obligation) -> tuple[str, dict[str, str]] | None:
    """Return (fact_id, constrained role->instance) whose absence makes the source witness invalid."""
    c = model.constraints.get(ob.id)
    if c is None:
        return None
    if c.kind == ConstraintKind.MANDATORY and c.fact_type_id and len(c.role_ids) == 1:
        fact = model.fact_types[c.fact_type_id]
        rid = c.role_ids[0]
        pos = next(r.ordinal for r in fact.roles if r.id == rid)
        role = next(r for r in fact.roles if r.id == rid)
        used = {row[pos] for row in pop.facts.get(fact.id, []) if len(row) > pos}
        candidates = sorted(pop.memberships.get(role.player_id, set()) - used)
        if candidates:
            return fact.id, {rid: candidates[0]}
        return None
    if c.kind in {ConstraintKind.SUBSET, ConstraintKind.EQUALITY} and c.fact_type_id and c.target_fact_type_id:
        left = model.fact_types[c.fact_type_id]
        rows = pop.facts.get(left.id, [])
        if not rows:
            return None
        row = rows[0]
        role_iids: dict[str, str] = {}
        for lrid, rrid in zip(c.role_ids, c.target_role_ids):
            lpos = next(r.ordinal for r in left.roles if r.id == lrid)
            role_iids[rrid] = row[lpos]
        return c.target_fact_type_id, role_iids
    if c.kind == ConstraintKind.RING and c.ring_kind == "symmetric" and c.fact_type_id:
        fact = model.fact_types[c.fact_type_id]
        rows = pop.facts.get(fact.id, [])
        if len(fact.roles) != 2 or not rows:
            return None
        row = rows[0]
        roles = sorted(fact.roles, key=lambda r: r.ordinal)
        return fact.id, {roles[0].id: row[roles[1].ordinal], roles[1].id: row[roles[0].ordinal]}
    return None


def _pg_fact_count_postcondition(model: Model, pop, fact_id: str, role_iids: dict[str, str]) -> dict[str, Any] | None:
    fact = model.fact_types[fact_id]
    clauses: list[str] = []
    filters: list[dict[str, str]] = []
    for role in sorted(fact.roles, key=lambda r: r.ordinal):
        if role.id not in role_iids:
            continue
        iid = role_iids[role.id]
        player = model.object_types[role.player_id]
        cols = postgres._role_columns(model, role)
        if isinstance(player, ValueType):
            if iid not in pop.values:
                return None
            literal = postgres._sql_literal(pop.values[iid], player.scalar_kind)
            clauses.append(f"{cols[0][0]} = {literal}")
            filters.append({"column": cols[0][0], "literal_sql": literal})
        elif isinstance(player, EntityType):
            pairs, problems = lowering._entity_key_values(model, pop, player, iid)
            if problems:
                return None
            hints = lowering._identifier_hints(model, player.id)
            for (col, _typ), (_field, value), hint in zip(cols, pairs, hints):
                vt = model.object_types[hint.value_type_id]
                assert isinstance(vt, ValueType)
                literal = postgres._sql_literal(value, vt.scalar_kind)
                clauses.append(f"{col} = {literal}")
                filters.append({"column": col, "literal_sql": literal})
        else:
            return None
    where = " AND ".join(clauses) if clauses else "TRUE"
    table = lowering.slug(fact.name)
    return {
        "op": "scalar_sql",
        "sql": f"SELECT COUNT(*) FROM {table} WHERE {where};",
        "table": table,
        "filters": filters,
        "expect_scalar": 0,
    }


def _mongo_fact_count_postcondition(model: Model, pop, fact_id: str, role_iids: dict[str, str]) -> dict[str, Any] | None:
    fact = model.fact_types[fact_id]
    filt: dict[str, Any] = {}
    for role in sorted(fact.roles, key=lambda r: r.ordinal):
        if role.id not in role_iids:
            continue
        iid = role_iids[role.id]
        player = model.object_types[role.player_id]
        names = list(mongo._role_properties(model, role))
        if isinstance(player, ValueType):
            if iid not in pop.values:
                return None
            filt[names[0]] = lowering._json_value(pop.values[iid])
        elif isinstance(player, EntityType):
            ids = lowering._identifier_hints(model, player.id)
            if not ids:
                filt[names[0]] = {"$fg_type": "objectId", "value": lowering._stable_object_id(iid)}
            else:
                pairs, problems = lowering._entity_key_values(model, pop, player, iid)
                if problems:
                    return None
                for name, (_field, value) in zip(names, pairs):
                    filt[name] = lowering._json_value(value)
        else:
            return None
    return {"op": "count_documents", "collection": lowering.slug(fact.name), "filter": filt, "expect_scalar": 0}


def _typedb_fact_count_postcondition(model: Model, pop, fact_id: str, role_iids: dict[str, str]) -> dict[str, Any] | None:
    fact = model.fact_types[fact_id]
    statements: list[str] = []
    links: list[str] = []
    has: list[str] = []
    for idx, role in enumerate(sorted(fact.roles, key=lambda r: r.ordinal)):
        if role.id not in role_iids:
            continue
        iid = role_iids[role.id]
        player = model.object_types[role.player_id]
        if isinstance(player, EntityType):
            ids = lowering._identifier_hints(model, player.id)
            if not ids:
                return None
            pairs, problems = lowering._entity_key_values(model, pop, player, iid)
            if problems:
                return None
            var = f"$p{idx}"
            chunks = [f"{var} isa {typedb.entity_label(player)}"]
            for hint, (_field, value) in zip(ids, pairs):
                vt = model.object_types[hint.value_type_id]
                assert isinstance(vt, ValueType)
                chunks.append(f"has {typedb.field_attribute_label(model, hint.field_fact_id)} {typedb._typeql_literal(value, vt.scalar_kind)}")
            statements.append(", ".join(chunks) + ";")
            links.append(f"{typedb.role_label(role.name)}: {var}")
        elif isinstance(player, ValueType):
            if iid not in pop.values:
                return None
            has.append(f"has {typedb.value_role_attribute_label(model, fact.id, role.id)} {typedb._typeql_literal(pop.values[iid], player.scalar_kind)}")
        else:
            return None
    rel_chunks = [f"$r isa {typedb.relation_label(model, fact.id)}"]
    if links:
        rel_chunks.append("links (" + ", ".join(links) + ")")
    rel_chunks.extend(has)
    statements.append(", ".join(rel_chunks) + ";")
    query = "match\n  " + "\n  ".join(statements) + "\nselect $r;"
    return {"op": "count_typeql", "typeql": query, "expect_scalar": 0}



def _mongo_subtype_postcondition(model: Model, pop, ob: Obligation) -> dict[str, Any] | None:
    c = model.constraints.get(ob.id)
    if c is None or c.kind != ConstraintKind.SUBTYPE or not c.subtype_id or not c.supertype_id:
        return None
    subtype = model.object_types.get(c.subtype_id)
    supertype = model.object_types.get(c.supertype_id)
    if not isinstance(subtype, EntityType) or not isinstance(supertype, EntityType):
        return None
    invalid = sorted(pop.memberships.get(subtype.id, set()) - pop.memberships.get(supertype.id, set()))
    if not invalid:
        return None
    iid = invalid[0]
    hints = lowering._identifier_hints(model, subtype.id)
    if not hints:
        filt = {"_id": {"$fg_type": "objectId", "value": lowering._stable_object_id(iid)}}
    else:
        filt: dict[str, Any] = {}
        for hint, value in lowering.subtype_support_key_values(model, subtype, iid):
            filt[lowering.slug(hint.field_name)] = lowering._json_value(value)
    return {
        "op": "count_documents",
        "collection": lowering.slug(supertype.name),
        "filter": filt,
        "expect_scalar": 0,
        "semantic_check": "subtype instance has no corresponding supertype document",
    }

def _postconditions(model: Model, pop, ob: Obligation, target: str, required: bool) -> tuple[dict[str, Any], ...]:
    if not required:
        return ()
    if ob.kind == "subtype":
        if target == "mongo":
            pc = _mongo_subtype_postcondition(model, pop, ob)
            return (pc,) if pc is not None else ()
        return ()
    pattern = _absence_pattern(model, pop, ob)
    if pattern is None:
        return ()
    fact_id, role_iids = pattern
    if target == "postgres":
        pc = _pg_fact_count_postcondition(model, pop, fact_id, role_iids)
    elif target == "mongo":
        pc = _mongo_fact_count_postcondition(model, pop, fact_id, role_iids)
    elif target == "typedb":
        pc = _typedb_fact_count_postcondition(model, pop, fact_id, role_iids)
    else:
        pc = None
    return (pc,) if pc is not None else ()


def build_cases(model: Model, target: str) -> list[SharedWitnessCase]:
    rows: list[SharedWitnessCase] = []
    for ob in obligations(model):
        source = synthesize_counterexample(model, ob.id, ob.kind)
        execution = prepare_execution_witness(model, source)
        program = lower_population(model, execution.population, target)
        cap = _capability(model, target, ob)
        if program.status != "lowered":
            expected = "not_executable"
        elif cap is None:
            expected = "not_asserted"
        elif cap.status in _ENFORCED:
            expected = "prevented"
        elif cap.status in _WEAK:
            expected = "realized"
        else:
            expected = "not_asserted"
        poststate_required = ob.kind in _ABSENCE_OR_ENTAILMENT_KINDS and expected == "realized"
        pcs = _postconditions(model, execution.population, ob, target, poststate_required and program.status == "lowered")
        rows.append(SharedWitnessCase(
            obligation_id=ob.id,
            kind=ob.kind,
            target=target,
            source_counterexample_status=source.status,
            execution_witness_mode=execution.mode,
            execution_context_added=execution.context_added,
            execution_context_note=execution.note,
            execution_population=execution.population.to_dict(model),
            lowering_status=program.status,
            lowering_fidelity=program.fidelity,
            expected_write_outcome=expected,
            poststate_required_for_semantic_proof=poststate_required,
            capability_status=cap.status.value if cap else None,
            capability_mechanism=cap.mechanism if cap else None,
            capability_reason=cap.reason if cap else None,
            postconditions=pcs,
            program=program,
        ))
    return rows


def model_semantic_sha256(model: Model) -> str:
    return hashlib.sha256(model.semantic_json(include_samples=False).encode("utf-8")).hexdigest()


def case_plan_sha256(cases: list[SharedWitnessCase]) -> str:
    payload = [case.to_dict() for case in cases]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evidence_identity(model: Model, target: str, cases: list[SharedWitnessCase] | None = None) -> dict[str, str]:
    cases = build_cases(model, target) if cases is None else cases
    return {
        "model_id": model.id,
        "model_semantic_sha256": model_semantic_sha256(model),
        "case_plan_sha256": case_plan_sha256(cases),
    }


def generated_report(model: Model, target: str) -> dict[str, Any]:
    cases = build_cases(model, target)
    identity = evidence_identity(model, target, cases)
    return {
        "format": "factgraph-shared-witness-execution-plan-v2",
        "target": target,
        "status": "generated_not_run",
        "model": model.name,
        **identity,
        "case_count": len(cases),
        "executable_case_count": sum(c.lowering_status == "lowered" for c in cases),
        "contextualized_execution_witness_count": sum(c.execution_context_added for c in cases),
        "poststate_required_count": sum(c.poststate_required_for_semantic_proof for c in cases),
        "poststate_query_ready_count": sum(c.poststate_required_for_semantic_proof and bool(c.postconditions) for c in cases),
        "cases": [c.to_dict() for c in cases],
    }


def _evaluate(
    case: SharedWitnessCase,
    actual_write_outcome: str | None,
    *,
    error: str | None = None,
    poststate_passed: bool | None = None,
    poststate_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected = case.expected_write_outcome
    if case.lowering_status != "lowered":
        return {
            "obligation_id": case.obligation_id,
            "kind": case.kind,
            "target": case.target,
            "status": "not_executable",
            "passed": None,
            "expected_write_outcome": expected,
            "actual_write_outcome": None,
            "semantic_evidence_level": "lowering_gap",
            "semantic_observation": "not_observed",
            "observed_preservation": None,
            "lowering_status": case.lowering_status,
            "lowering_fidelity": case.lowering_fidelity,
            "limitations": list(case.program.limitations),
        }
    write_matches = None if expected == "not_asserted" else actual_write_outcome == expected
    if actual_write_outcome == "realized" and case.poststate_required_for_semantic_proof:
        if write_matches is None:
            passed = None
        elif not write_matches:
            passed = False
        elif poststate_passed is None:
            passed = None
        else:
            passed = bool(poststate_passed)
        if poststate_passed is True:
            semantic_level = "invalid_population_state_verified"
        elif poststate_passed is False:
            semantic_level = "write_accepted_but_invalid_state_not_realized"
        else:
            semantic_level = "write_realization_observed_poststate_pending"
    else:
        passed = write_matches
        if actual_write_outcome == "prevented":
            semantic_level = "negative_write_prevention_observed"
        elif actual_write_outcome == "realized":
            semantic_level = "invalid_population_write_realized"
        else:
            semantic_level = "write_outcome_unknown"

    if actual_write_outcome == "prevented":
        semantic_observation = "invalid_population_prevented"
        observed_preservation = "preserved_or_stronger"
    elif actual_write_outcome == "realized" and case.poststate_required_for_semantic_proof:
        if poststate_passed is True:
            semantic_observation = "invalid_population_realized"
            observed_preservation = "weakened"
        elif poststate_passed is False:
            semantic_observation = "invalid_population_not_realized_after_write"
            observed_preservation = "preserved_or_stronger"
        else:
            semantic_observation = "poststate_pending"
            observed_preservation = None
    elif actual_write_outcome == "realized":
        semantic_observation = "invalid_population_realized"
        observed_preservation = "weakened"
    else:
        semantic_observation = "not_observed"
        observed_preservation = None
    return {
        "obligation_id": case.obligation_id,
        "kind": case.kind,
        "target": case.target,
        "status": "observed",
        "passed": passed,
        "expected_write_outcome": expected,
        "actual_write_outcome": actual_write_outcome,
        "semantic_evidence_level": semantic_level,
        "semantic_observation": semantic_observation,
        "observed_preservation": observed_preservation,
        "poststate_required_for_semantic_proof": case.poststate_required_for_semantic_proof,
        "poststate_query_count": len(case.postconditions),
        "poststate_passed": poststate_passed,
        "poststate_results": poststate_results or [],
        "capability_status": case.capability_status,
        "error": error,
    }


def _split_sql(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def _unavailable_report(model: Model, target: str, cases: list[SharedWitnessCase], reason: str) -> dict[str, Any]:
    return {
        "format": "factgraph-shared-witness-live-v2",
        "target": target,
        "status": "unavailable",
        "reason": reason,
        "model": model.name,
        **evidence_identity(model, target, cases),
        "case_count": len(cases),
        "asserted_case_count": 0,
        "poststate_pending_count": 0,
        "passed": None,
        "results": [],
    }


def run_postgres(model: Model, dsn: str) -> dict[str, Any]:
    cases = build_cases(model, "postgres")
    try:
        import psycopg  # type: ignore
    except ImportError:
        return _unavailable_report(model, "postgres", cases, "psycopg is not installed")
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception as exc:
        return _unavailable_report(model, "postgres", cases, f"connection failed: {type(exc).__name__}: {exc}")
    results: list[dict[str, Any]] = []
    schema_sql = postgres.emit_sql(model)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
        for case in cases:
            if case.lowering_status != "lowered":
                results.append(_evaluate(case, None)); continue
            schema = "fg_sw_" + hashlib.sha256(f"{model.id}\0{case.obligation_id}".encode()).hexdigest()[:12]
            actual = "realized"; err = None
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                    cur.execute(f'CREATE SCHEMA "{schema}"')
                    cur.execute(f'SET search_path TO "{schema}"')
                    for stmt in _split_sql(schema_sql):
                        cur.execute(stmt)
                    for op in case.program.operations:
                        cur.execute(op["sql"].rstrip(";"))
            except Exception as exc:
                actual = "prevented"; err = f"{type(exc).__name__}: {exc}"
            poststate_passed: bool | None = None
            poststate_results: list[dict[str, Any]] = []
            if actual == "realized" and case.postconditions:
                poststate_passed = True
                for condition in case.postconditions:
                    try:
                        if condition.get("op") != "scalar_sql":
                            raise ValueError(f"unsupported PostgreSQL postcondition op: {condition.get('op')}")
                        with conn.cursor() as cur:
                            cur.execute(condition["sql"])
                            row = cur.fetchone()
                        actual_scalar = row[0] if row is not None else None
                        expected_scalar = condition.get("expect_scalar")
                        condition_passed = actual_scalar == expected_scalar
                        poststate_results.append({
                            "op": condition["op"],
                            "query": condition["sql"],
                            "expected_scalar": expected_scalar,
                            "actual_scalar": actual_scalar,
                            "passed": condition_passed,
                        })
                        poststate_passed = bool(poststate_passed and condition_passed)
                    except Exception as exc:
                        poststate_passed = False
                        poststate_results.append({
                            "op": condition.get("op"),
                            "query": condition.get("sql"),
                            "passed": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
            try:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            except Exception:
                pass
            results.append(_evaluate(
                case, actual, error=err,
                poststate_passed=poststate_passed, poststate_results=poststate_results,
            ))
        asserted = [r for r in results if r.get("passed") is not None]
        pending = [r for r in results if r.get("semantic_evidence_level") == "write_realization_observed_poststate_pending"]
        return {
            "format": "factgraph-shared-witness-live-v2", "target": "postgres", "status": "completed", "server": version,
            "model": model.name, **evidence_identity(model, "postgres", cases),
            "case_count": len(results), "asserted_case_count": len(asserted),
            "poststate_pending_count": len(pending),
            "passed": all(r["passed"] for r in asserted) and not pending, "results": results,
        }
    finally:
        conn.close()


def _mongo_decode(value: Any, ObjectId: Any, Decimal128: Any) -> Any:
    if isinstance(value, list):
        return [_mongo_decode(v, ObjectId, Decimal128) for v in value]
    if isinstance(value, dict):
        marker = value.get("$fg_type")
        if marker == "objectId": return ObjectId(value["value"])
        if marker == "decimal": return Decimal128(value["value"])
        if marker == "date":
            from datetime import datetime
            return datetime.fromisoformat(value["value"] + "T00:00:00+00:00")
        if marker == "datetime":
            from datetime import datetime
            return datetime.fromisoformat(value["value"])
        return {k: _mongo_decode(v, ObjectId, Decimal128) for k, v in value.items()}
    return value


def run_mongo(model: Model, uri: str) -> dict[str, Any]:
    cases = build_cases(model, "mongo")
    try:
        from pymongo import MongoClient  # type: ignore
        from bson import ObjectId, Decimal128  # type: ignore
    except ImportError:
        return _unavailable_report(model, "mongo", cases, "pymongo is not installed")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        version = client.server_info().get("version")
    except Exception as exc:
        return _unavailable_report(model, "mongo", cases, f"connection failed: {type(exc).__name__}: {exc}")
    spec = __import__("json").loads(mongo.emit_spec_json(model))
    results: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.lowering_status != "lowered":
                results.append(_evaluate(case, None)); continue
            db_name = "fg_sw_" + hashlib.sha256(f"{model.id}\0{case.obligation_id}".encode()).hexdigest()[:12]
            actual = "realized"; err = None
            try:
                client.drop_database(db_name)
                db = client[db_name]
                for coll in spec["collections"]:
                    db.create_collection(coll["name"], validator=coll["validator"])
                    for idx in coll["indexes"]:
                        db[coll["name"]].create_index(list(idx["keys"].items()), unique=bool(idx.get("unique")))
                for op in case.program.operations:
                    db[op["collection"]].insert_one(_mongo_decode(op["document"], ObjectId, Decimal128))
            except Exception as exc:
                actual = "prevented"; err = f"{type(exc).__name__}: {exc}"
            poststate_passed: bool | None = None
            poststate_results: list[dict[str, Any]] = []
            if actual == "realized" and case.postconditions:
                poststate_passed = True
                for condition in case.postconditions:
                    try:
                        if condition.get("op") != "count_documents":
                            raise ValueError(f"unsupported MongoDB postcondition op: {condition.get('op')}")
                        filt = _mongo_decode(condition.get("filter", {}), ObjectId, Decimal128)
                        actual_scalar = db[condition["collection"]].count_documents(filt)
                        expected_scalar = condition.get("expect_scalar")
                        condition_passed = actual_scalar == expected_scalar
                        poststate_results.append({
                            "op": condition["op"],
                            "collection": condition["collection"],
                            "filter": condition.get("filter", {}),
                            "expected_scalar": expected_scalar,
                            "actual_scalar": actual_scalar,
                            "passed": condition_passed,
                        })
                        poststate_passed = bool(poststate_passed and condition_passed)
                    except Exception as exc:
                        poststate_passed = False
                        poststate_results.append({
                            "op": condition.get("op"),
                            "collection": condition.get("collection"),
                            "filter": condition.get("filter", {}),
                            "passed": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
            try:
                client.drop_database(db_name)
            except Exception:
                pass
            results.append(_evaluate(
                case, actual, error=err,
                poststate_passed=poststate_passed, poststate_results=poststate_results,
            ))
        asserted = [r for r in results if r.get("passed") is not None]
        pending = [r for r in results if r.get("semantic_evidence_level") == "write_realization_observed_poststate_pending"]
        return {
            "format": "factgraph-shared-witness-live-v2", "target": "mongo", "status": "completed", "server": version,
            "model": model.name, **evidence_identity(model, "mongo", cases),
            "case_count": len(results), "asserted_case_count": len(asserted),
            "poststate_pending_count": len(pending),
            "passed": all(r["passed"] for r in asserted) and not pending, "results": results,
        }
    finally:
        client.close()


def run_typedb(model: Model, address: str, *, username: str = "admin", password: str = "password", tls: bool = False) -> dict[str, Any]:
    cases = build_cases(model, "typedb")
    try:
        from typedb.driver import TypeDB, TransactionType, Credentials, DriverOptions, DriverTlsConfig  # type: ignore
    except ImportError:
        return _unavailable_report(model, "typedb", cases, "typedb-driver is not installed")
    tls_config = DriverTlsConfig.enabled_with_native_root_ca() if tls else DriverTlsConfig.disabled()
    try:
        driver = TypeDB.driver(address, Credentials(username, password), DriverOptions(tls_config))
    except Exception as exc:
        return _unavailable_report(model, "typedb", cases, f"connection failed: {type(exc).__name__}: {exc}")
    from .live.typedb import _delete_database
    schema = typedb.emit_schema(model)
    results: list[dict[str, Any]] = []
    try:
        for case in cases:
            if case.lowering_status != "lowered":
                results.append(_evaluate(case, None)); continue
            db_name = "fg-sw-" + hashlib.sha256(f"{model.id}\0{case.obligation_id}".encode()).hexdigest()[:16]
            actual = "realized"; err = None
            try:
                _delete_database(driver, db_name)
                driver.databases.create(db_name)
                with driver.transaction(db_name, TransactionType.SCHEMA) as tx:
                    tx.query(schema).resolve(); tx.commit()
                for op in case.program.operations:
                    with driver.transaction(db_name, TransactionType.WRITE) as tx:
                        tx.query(op["typeql"]).resolve(); tx.commit()
            except Exception as exc:
                actual = "prevented"; err = f"{type(exc).__name__}: {exc}"
            poststate_passed: bool | None = None
            poststate_results: list[dict[str, Any]] = []
            if actual == "realized" and case.postconditions:
                poststate_passed = True
                for condition in case.postconditions:
                    try:
                        if condition.get("op") != "count_typeql":
                            raise ValueError(f"unsupported TypeDB postcondition op: {condition.get('op')}")
                        with driver.transaction(db_name, TransactionType.READ) as tx:
                            answer = tx.query(condition["typeql"]).resolve()
                            actual_scalar = sum(1 for _ in answer.as_concept_rows())
                        expected_scalar = condition.get("expect_scalar")
                        condition_passed = actual_scalar == expected_scalar
                        poststate_results.append({
                            "op": condition["op"],
                            "query": condition["typeql"],
                            "expected_scalar": expected_scalar,
                            "actual_scalar": actual_scalar,
                            "passed": condition_passed,
                        })
                        poststate_passed = bool(poststate_passed and condition_passed)
                    except Exception as exc:
                        poststate_passed = False
                        poststate_results.append({
                            "op": condition.get("op"),
                            "query": condition.get("typeql"),
                            "passed": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        })
            try:
                _delete_database(driver, db_name)
            except Exception:
                pass
            results.append(_evaluate(
                case, actual, error=err,
                poststate_passed=poststate_passed, poststate_results=poststate_results,
            ))
        asserted = [r for r in results if r.get("passed") is not None]
        pending = [r for r in results if r.get("semantic_evidence_level") == "write_realization_observed_poststate_pending"]
        return {
            "format": "factgraph-shared-witness-live-v2", "target": "typedb", "status": "completed", "server": address,
            "model": model.name, **evidence_identity(model, "typedb", cases),
            "case_count": len(results), "asserted_case_count": len(asserted),
            "poststate_pending_count": len(pending),
            "passed": all(r["passed"] for r in asserted) and not pending, "results": results,
        }
    finally:
        try: driver.close()
        except Exception: pass


def run(model: Model, target: str, connection: str, **kwargs: Any) -> dict[str, Any]:
    if target == "postgres": return run_postgres(model, connection)
    if target == "mongo": return run_mongo(model, connection)
    if target == "typedb": return run_typedb(model, connection, **kwargs)
    raise ValueError(f"unknown target {target}")
