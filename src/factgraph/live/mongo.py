from __future__ import annotations

from datetime import datetime
from typing import Any
import hashlib
import re

from ..conformance import mongo_cases
from ..model import Model
from ..targets import mongo


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


def run(model: Model, uri: str, *, spec_override: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        from pymongo import MongoClient  # type: ignore
        from bson import ObjectId, Decimal128  # type: ignore
    except ImportError:
        return {
            "target": "mongo",
            "status": "unavailable",
            "reason": "pymongo is not installed; install factgraph[conformance]",
            "results": [],
        }

    cases = mongo_cases(model)
    runtime_cases = [c for c in cases if c.mode == "runtime"]
    structural_cases = [c for c in cases if c.mode == "structural"]
    results: list[dict[str, Any]] = [
        {
            "case_id": c.id,
            "mode": "structural",
            "passed": bool(c.static_pass),
            "expected": "pass",
            "actual": "pass" if c.static_pass else "fail",
            "evidence": c.evidence,
        }
        for c in structural_cases
    ]
    db_name = "fg_conf_" + hashlib.sha256(model.id.encode()).hexdigest()[:12]
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        ping = client.admin.command("ping")
        info = client.server_info()
    except Exception as exc:
        return {
            "target": "mongo",
            "status": "unavailable",
            "reason": f"connection failed: {type(exc).__name__}: {exc}",
            "results": results,
        }

    spec = spec_override if spec_override is not None else __import__("json").loads(mongo.emit_spec_json(model))
    try:
        for case in runtime_cases:
            client.drop_database(db_name)
            db = client[db_name]
            for coll in spec["collections"]:
                db.create_collection(coll["name"], validator=coll["validator"])
                for index in coll["indexes"]:
                    db[coll["name"]].create_index(list(index["keys"].items()), unique=bool(index.get("unique")))
            for op in case.setup:
                if op["op"] != "insert_one":
                    raise ValueError(f"unknown Mongo conformance setup op {op['op']}")
                db[op["collection"]].insert_one(_decode(op["document"], ObjectId, Decimal128))
            case_pass = True
            step_rows: list[dict[str, Any]] = []
            for step in case.steps:
                if step["op"] == "count_documents":
                    try:
                        actual_value = db[step["collection"]].count_documents(_decode(step.get("filter", {}), ObjectId, Decimal128))
                        err = None
                    except Exception as exc:
                        actual_value = None
                        err = f"{type(exc).__name__}: {exc}"
                    expected_value = step["expect_scalar"]
                    passed = actual_value == expected_value and err is None
                    case_pass = case_pass and passed
                    step_rows.append({"expected_scalar": expected_value, "actual_scalar": actual_value, "passed": passed, "error": err})
                    continue
                expected = step["expect"]
                try:
                    if step["op"] != "insert_one":
                        raise ValueError(f"unknown Mongo conformance op {step['op']}")
                    db[step["collection"]].insert_one(_decode(step["document"], ObjectId, Decimal128))
                    actual = "accept"
                    err = None
                except Exception as exc:
                    actual = "reject"
                    err = f"{type(exc).__name__}: {exc}"
                passed = actual == expected
                case_pass = case_pass and passed
                step_rows.append({"expected": expected, "actual": actual, "passed": passed, "error": err})
            results.append({
                "case_id": case.id,
                "feature": case.feature,
                "mode": "runtime",
                "passed": case_pass,
                "description": case.description,
                "steps": step_rows,
            })
        client.drop_database(db_name)
        return {
            "target": "mongo",
            "status": "completed",
            "server": info.get("version"),
            "model": model.name,
            "passed": all(r["passed"] for r in results),
            "case_count": len(results),
            "results": results,
        }
    finally:
        client.close()
