from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
from pathlib import Path
from typing import Any

from ..diff import ChangeSafety, MigrationHints
from ..model import Model
from ..migrations import mongo as mongo_migration
from ..migrations.base import TargetMigrationOperation
from ..targets import mongo
from .migration_common import (
    MigrationExecutionPolicy,
    initial_operation_result,
    load_fixture,
    operation_selected,
    plan_expected_complete,
)


def _decode(value: Any, ObjectId, Decimal128) -> Any:
    if isinstance(value, list):
        return [_decode(v, ObjectId, Decimal128) for v in value]
    if isinstance(value, dict):
        marker = value.get("$fg_type")
        if marker == "objectId":
            return ObjectId(value["value"])
        if marker == "decimal":
            return Decimal128(value["value"])
        if marker == "date":
            return datetime.fromisoformat(value["value"] + "T00:00:00+00:00")
        if marker == "datetime":
            return datetime.fromisoformat(value["value"])
        return {k: _decode(v, ObjectId, Decimal128) for k, v in value.items()}
    return value


def expected_snapshot(model: Model) -> dict[str, Any]:
    spec = mongo.build_plan(model)
    collections = {}
    for c in spec["collections"]:
        collections[c["name"]] = {
            "validator": c["validator"],
            "indexes": sorted(
                [{"keys": list(i["keys"].items()), "unique": bool(i.get("unique"))} for i in c["indexes"]],
                key=lambda x: (x["keys"], x["unique"]),
            ),
        }
    return {"collections": collections}


def live_snapshot(db) -> dict[str, Any]:
    collections = {}
    for name in sorted(db.list_collection_names()):
        options = db.command({"listCollections": 1, "filter": {"name": name}})["cursor"]["firstBatch"][0].get("options", {})
        indexes = []
        for idx in db[name].list_indexes():
            if idx.get("name") == "_id_":
                continue
            indexes.append({"keys": list(idx["key"].items()), "unique": bool(idx.get("unique", False))})
        collections[name] = {
            "validator": options.get("validator", {}),
            "indexes": sorted(indexes, key=lambda x: (x["keys"], x["unique"])),
        }
    return {"collections": collections}


def compare_snapshot(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    exp, act = expected["collections"], actual["collections"]
    mismatches: list[dict[str, Any]] = []
    if sorted(exp) != sorted(act):
        mismatches.append({"kind": "collection_names", "expected": sorted(exp), "actual": sorted(act)})
    for name in sorted(set(exp) & set(act)):
        for key in ["validator", "indexes"]:
            if exp[name][key] != act[name][key]:
                mismatches.append({"kind": key, "collection": name, "expected": exp[name][key], "actual": act[name][key]})
    return {"passed": not mismatches, "mismatches": mismatches, "expected": expected, "actual": actual}


def _run_preflight(db, op: TargetMigrationOperation, ObjectId, Decimal128) -> dict[str, Any]:
    p = op.parameters
    coll = p.get("collection") or op.target_object
    if op.safety == ChangeSafety.DESTRUCTIVE:
        if op.kind == "drop_collection":
            return {"passed": True, "kind": "impact_only", "count": db[coll].count_documents({})}
        if op.kind == "unset_removed_field":
            field = p["field"]
            return {"passed": True, "kind": "impact_only", "count": db[coll].count_documents({field: {"$exists": True}})}
        return {"passed": True, "kind": "impact_only"}
    if op.kind in {"rename_field_data", "rename_indexed_field"}:
        count = db[coll].count_documents({p["new_field"]: {"$exists": True}})
        return {"passed": count == 0, "kind": "destination_collision", "count": count}
    if op.kind == "create_index" and p.get("index", {}).get("unique"):
        fields = list(p["index"]["keys"])
        gid = {f: f"${f}" for f in fields}
        rows = list(db[coll].aggregate([{"$group": {"_id": gid, "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}, {"$limit": 20}]))
        return {"passed": not rows, "kind": "duplicate_probe", "rows": rows}
    if op.kind == "update_validator":
        validator = p["validator"]
        clauses = []
        if "$jsonSchema" in validator:
            clauses.append({"$nor": [{"$jsonSchema": validator["$jsonSchema"]}]})
        if "$expr" in validator:
            clauses.append({"$expr": {"$not": [validator["$expr"]]}})
        query = clauses[0] if len(clauses) == 1 else {"$or": clauses} if clauses else {}
        rows = list(db[coll].find(query).limit(20))
        return {"passed": not rows, "kind": "validator_probe", "row_count": len(rows)}
    if op.kind == "relax_validator_for_rename":
        return {"passed": True, "kind": "transition_informational"}
    return {"passed": False, "kind": "missing_structured_preflight", "operation_kind": op.kind}


def _execute(db, op: TargetMigrationOperation) -> dict[str, Any]:
    p = op.parameters
    coll = p.get("collection") or op.target_object
    if op.kind == "create_collection":
        spec = p["spec"]
        db.create_collection(spec["name"], validator=spec["validator"])
        for idx in spec["indexes"]:
            db[spec["name"]].create_index(list(idx["keys"].items()), unique=bool(idx.get("unique")))
    elif op.kind == "drop_collection":
        db[coll].drop()
    elif op.kind == "rename_collection":
        db[coll].rename(p["new_name"])
    elif op.kind in {"relax_validator_for_rename", "update_validator"}:
        db.command({"collMod": coll, "validator": p["validator"]})
    elif op.kind in {"rename_field_data", "rename_indexed_field"}:
        db[coll].update_many({p["old_field"]: {"$exists": True}}, {"$rename": {p["old_field"]: p["new_field"]}})
    elif op.kind == "unset_removed_field":
        db[coll].update_many({p["field"]: {"$exists": True}}, {"$unset": {p["field"]: ""}})
    elif op.kind == "create_index":
        idx = p["index"]
        db[coll].create_index(list(idx["keys"].items()), unique=bool(idx.get("unique")))
    elif op.kind == "drop_index":
        db[coll].drop_index(p["index_name"])
    else:
        raise ValueError(f"no MongoDB live executor for migration operation {op.kind}")
    return {"passed": True}


def _apply_fixture(db, fixture: dict[str, Any], ObjectId, Decimal128) -> None:
    for op in fixture.get("mongo", {}).get("setup", []):
        kind = op.get("op")
        if kind == "insert_one":
            db[op["collection"]].insert_one(_decode(op["document"], ObjectId, Decimal128))
        elif kind == "insert_many":
            db[op["collection"]].insert_many(_decode(op["documents"], ObjectId, Decimal128))
        else:
            raise ValueError(f"unknown MongoDB migration fixture op {kind}")


def _create_model(db, model: Model) -> None:
    spec = mongo.build_plan(model)
    for coll in spec["collections"]:
        db.create_collection(coll["name"], validator=coll["validator"])
        for idx in coll["indexes"]:
            db[coll["name"]].create_index(list(idx["keys"].items()), unique=bool(idx.get("unique")))


def run(
    before: Model,
    after: Model,
    uri: str,
    *,
    hints: MigrationHints | None = None,
    fixture_path: Path | None = None,
    policy: MigrationExecutionPolicy | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    policy = policy or MigrationExecutionPolicy()
    hints = hints or MigrationHints.empty()
    fixture = load_fixture(fixture_path)
    try:
        from pymongo import MongoClient  # type: ignore
        from bson import ObjectId, Decimal128  # type: ignore
    except ImportError:
        return {"target": "mongo", "status": "unavailable", "reason": "pymongo is not installed; install factgraph[conformance]", "operations": []}

    seed = hashlib.sha256(f"{before.id}|{after.id}".encode()).hexdigest()[:12]
    db_name = f"fg_mig_{seed}"
    plan = mongo_migration.build_plan(before, after, hints)
    operation_rows = [initial_operation_result(op, policy) for op in plan.operations]
    expected_complete = plan_expected_complete(plan, policy)
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        server = client.server_info().get("version")
    except Exception as exc:
        return {"target": "mongo", "status": "unavailable", "reason": f"connection failed: {type(exc).__name__}: {exc}", "operations": operation_rows}

    report: dict[str, Any] = {
        "format": "factgraph-live-migration-report-v1",
        "target": "mongo",
        "before_model": before.name,
        "after_model": after.name,
        "namespace": db_name,
        "transactional": False,
        "transaction_note": "MongoDB collection/index/validator DDL is not treated as one generic transaction; the harness uses an isolated disposable database and drops it on failure unless --keep is set.",
        "server": server,
        "policy": policy.to_dict(),
        "fixture": str(fixture_path) if fixture_path else None,
        "expected_complete": expected_complete,
        "operations": operation_rows,
        "status": "running",
    }
    db = client[db_name]
    try:
        client.drop_database(db_name)
        db = client[db_name]
        _create_model(db, before)
        _apply_fixture(db, fixture, ObjectId, Decimal128)
        report["before_verification"] = compare_snapshot(expected_snapshot(before), live_snapshot(db))

        failed = None
        for op, row in zip(plan.operations, operation_rows):
            if not operation_selected(op, policy):
                continue
            if op.safety in {ChangeSafety.REQUIRES_DATA_CHECK, ChangeSafety.DESTRUCTIVE}:
                try:
                    pf = _run_preflight(db, op, ObjectId, Decimal128)
                except Exception as exc:
                    pf = {"passed": False, "kind": "preflight_error", "error": f"{type(exc).__name__}: {exc}"}
                row["preflight"] = pf
                if op.safety == ChangeSafety.REQUIRES_DATA_CHECK and not pf.get("passed"):
                    row["status"] = "preflight_failed"
                    failed = {"operation_id": op.id, "stage": "preflight", "detail": pf}
                    break
            try:
                row["execution"] = _execute(db, op)
                row["status"] = "executed"
            except Exception as exc:
                row["status"] = "execution_failed"
                row["execution"] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
                failed = {"operation_id": op.id, "stage": "execution", "detail": row["execution"]}
                break

        if failed:
            report["failure"] = failed
            report["status"] = "preflight_failed" if failed["stage"] == "preflight" else "execution_failed"
            report["verification"] = {"passed": None, "reason": "partial MongoDB migration state is not presented as verified; isolated database is discarded unless --keep was requested"}
        else:
            actual = live_snapshot(db)
            report["verification"] = compare_snapshot(expected_snapshot(after), actual)
            if expected_complete:
                report["status"] = "completed" if report["verification"]["passed"] else "verification_failed"
            else:
                report["status"] = "completed_partial"
        return report
    finally:
        if not keep:
            try:
                client.drop_database(db_name)
            except Exception:
                pass
        client.close()
