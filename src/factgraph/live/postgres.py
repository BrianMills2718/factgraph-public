from __future__ import annotations

import hashlib
from typing import Any

from ..conformance import ConformanceCase, postgres_cases
from ..model import Model
from ..targets import postgres
from ..sqltext import split_sql


def _split_sql(sql: str) -> list[str]:
    # Comment- and quote-aware: the generated header comment itself contains a semicolon.
    return split_sql(sql)


def run(model: Model, dsn: str, *, schema_sql_override: str | None = None) -> dict[str, Any]:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        return {
            "target": "postgres",
            "status": "unavailable",
            "reason": "psycopg is not installed; install factgraph[conformance]",
            "results": [],
        }

    cases = postgres_cases(model)
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
    schema_seed = hashlib.sha256(model.id.encode()).hexdigest()[:12]
    schema = f"fg_conf_{schema_seed}"

    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception as exc:  # target environment error, not a model failure
        return {
            "target": "postgres",
            "status": "unavailable",
            "reason": f"connection failed: {type(exc).__name__}: {exc}",
            "results": results,
        }

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
        schema_sql = schema_sql_override if schema_sql_override is not None else postgres.emit_sql(model)
        for case in runtime_cases:
            case_pass = True
            step_rows: list[dict[str, Any]] = []
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                cur.execute(f'CREATE SCHEMA "{schema}"')
                cur.execute(f'SET search_path TO "{schema}"')
                for stmt in _split_sql(schema_sql):
                    cur.execute(stmt)
                for stmt in case.setup:
                    cur.execute(str(stmt).rstrip(";"))
                for step in case.steps:
                    if "expect_scalar" in step:
                        try:
                            cur.execute(step["sql"].rstrip(";"))
                            actual_value = cur.fetchone()[0]
                            err = None
                        except Exception as exc:
                            actual_value = None
                            err = f"{type(exc).__name__}: {exc}"
                        expected_value = step["expect_scalar"]
                        passed = actual_value == expected_value and err is None
                        case_pass = case_pass and passed
                        step_rows.append({"expected_scalar": expected_value, "actual_scalar": actual_value, "passed": passed, "error": err, "sql": step["sql"]})
                        continue
                    expected = step["expect"]
                    try:
                        cur.execute(step["sql"].rstrip(";"))
                        actual = "accept"
                        err = None
                    except Exception as exc:
                        actual = "reject"
                        err = f"{type(exc).__name__}: {exc}"
                    passed = actual == expected
                    case_pass = case_pass and passed
                    step_rows.append({"expected": expected, "actual": actual, "passed": passed, "error": err, "sql": step["sql"]})
            results.append({
                "case_id": case.id,
                "feature": case.feature,
                "mode": "runtime",
                "passed": case_pass,
                "description": case.description,
                "steps": step_rows,
            })
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        return {
            "target": "postgres",
            "status": "completed",
            "server": version,
            "model": model.name,
            "passed": all(r["passed"] for r in results),
            "case_count": len(results),
            "results": results,
        }
    finally:
        conn.close()
