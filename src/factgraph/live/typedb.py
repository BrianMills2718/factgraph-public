from __future__ import annotations

import hashlib
from typing import Any

from ..conformance import typedb_cases
from ..model import Model
from ..targets import typedb


def _database_name(model: Model, case_id: str) -> str:
    # TypeDB databases are isolated units. Use a short deterministic, portable label.
    digest = hashlib.sha256(f"{model.id}\0{case_id}".encode()).hexdigest()[:16]
    return f"fg-conf-{digest}"


def _delete_database(driver: Any, name: str) -> None:
    """Delete across the current driver API and the documented compatibility shape."""
    if not driver.databases.contains(name):
        return
    db = driver.databases.get(name)
    if hasattr(db, "delete"):
        db.delete()
        return
    delete = getattr(driver.databases, "delete", None)
    if delete is not None:
        delete(name)
        return
    raise RuntimeError("connected TypeDB driver exposes no supported database deletion method")


def _run_connected(model: Model, driver: Any, TransactionType: Any, *, schema_override: str | None = None) -> dict[str, Any]:
    cases = typedb_cases(model)
    structural_cases = [c for c in cases if c.mode == "structural"]
    runtime_cases = [c for c in cases if c.mode == "runtime"]
    results: list[dict[str, Any]] = [
        {
            "case_id": c.id,
            "feature": c.feature,
            "mode": "structural",
            "passed": bool(c.static_pass),
            "expected": "pass",
            "actual": "pass" if c.static_pass else "fail",
            "evidence": c.evidence,
        }
        for c in structural_cases
    ]
    schema = schema_override if schema_override is not None else typedb.emit_schema(model)

    for case in runtime_cases:
        db_name = _database_name(model, case.id)
        step_rows: list[dict[str, Any]] = []
        case_pass = True
        try:
            _delete_database(driver, db_name)
            driver.databases.create(db_name)
            with driver.transaction(db_name, TransactionType.SCHEMA) as tx:
                tx.query(schema).resolve()
                tx.commit()

            for step in case.steps:
                query = step.get("typeql")
                expected = step.get("expect")
                if not query or expected not in {"accept", "reject"}:
                    case_pass = False
                    step_rows.append({
                        "passed": False,
                        "error": "unsupported TypeDB conformance step; expected typeql + accept/reject",
                        "step": step,
                    })
                    continue
                try:
                    with driver.transaction(db_name, TransactionType.WRITE) as tx:
                        tx.query(query).resolve()
                        tx.commit()
                    actual = "accept"
                    err = None
                except Exception as exc:
                    actual = "reject"
                    err = f"{type(exc).__name__}: {exc}"
                passed = actual == expected
                case_pass = case_pass and passed
                step_rows.append({
                    "expected": expected,
                    "actual": actual,
                    "passed": passed,
                    "error": err,
                    "typeql": query,
                    "transaction": step.get("transaction", "write_commit"),
                })
        except Exception as exc:
            case_pass = False
            step_rows.append({
                "passed": False,
                "error": f"case setup failed: {type(exc).__name__}: {exc}",
                "schema": schema,
            })
        finally:
            try:
                _delete_database(driver, db_name)
            except Exception as exc:
                case_pass = False
                step_rows.append({
                    "passed": False,
                    "error": f"database cleanup failed: {type(exc).__name__}: {exc}",
                })
        results.append({
            "case_id": case.id,
            "feature": case.feature,
            "mode": "runtime",
            "passed": case_pass,
            "description": case.description,
            "steps": step_rows,
        })

    return {
        "target": "typedb",
        "status": "completed",
        "model": model.name,
        "passed": all(r.get("passed", False) for r in results),
        "case_count": len(results),
        "runtime_case_count": len(runtime_cases),
        "results": results,
    }


def run(
    model: Model,
    address: str,
    *,
    username: str = "admin",
    password: str = "password",
    tls: bool = False,
    schema_override: str | None = None,
) -> dict[str, Any]:
    """Execute TypeDB structural/live semantic conformance cases against an isolated server.

    Runtime acceptance/rejection is observed at transaction commit because TypeDB can
    validate cardinality/key constraints at commit. Each runtime case gets its own
    temporary database and is deleted afterward.
    """
    try:
        from typedb.driver import (  # type: ignore
            TypeDB,
            TransactionType,
            Credentials,
            DriverOptions,
            DriverTlsConfig,
        )
    except ImportError:
        return {
            "target": "typedb",
            "status": "unavailable",
            "reason": "typedb-driver is not installed; install factgraph[typedb]",
            "results": [],
        }

    tls_config = DriverTlsConfig.enabled_with_native_root_ca() if tls else DriverTlsConfig.disabled()
    try:
        driver = TypeDB.driver(address, Credentials(username, password), DriverOptions(tls_config))
    except Exception as exc:
        return {
            "target": "typedb",
            "status": "unavailable",
            "reason": f"connection failed: {type(exc).__name__}: {exc}",
            "address": address,
            "results": [],
        }

    try:
        report = _run_connected(model, driver, TransactionType, schema_override=schema_override)
        report["server"] = address
        report["credentials"] = {"username": username, "password": "<redacted>", "tls": tls}
        return report
    except Exception as exc:
        return {
            "target": "typedb",
            "status": "unavailable",
            "reason": f"live run failed before completion: {type(exc).__name__}: {exc}",
            "address": address,
            "results": [],
        }
    finally:
        try:
            driver.close()
        except Exception:
            pass
