from __future__ import annotations

import hashlib
import re
from typing import Any

from ..diff import ChangeSafety, MigrationHints
from ..model import Model
from ..migrations import postgres as pg_migration
from ..migrations.base import TargetMigrationOperation
from ..targets import postgres
from ..sqltext import split_sql
from .migration_common import (
    MigrationExecutionPolicy,
    initial_operation_result,
    load_fixture,
    operation_selected,
    plan_expected_complete,
)


def _split_sql(sql: str) -> list[str]:
    # Comment- and quote-aware: the generated header comment itself contains a semicolon.
    return split_sql(sql)


def _type_name(data_type: str, udt_name: str) -> str:
    mapping = {
        "text": "TEXT",
        "bigint": "BIGINT",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "double precision": "DOUBLE PRECISION",
        "numeric": "NUMERIC",
        "date": "DATE",
        "timestamp with time zone": "TIMESTAMPTZ",
        "uuid": "UUID",
    }
    return mapping.get(data_type, udt_name.upper())


def _normalize_check(expr: str) -> str:
    s = expr.strip()
    if s.upper().startswith("CHECK"):
        s = s[5:].strip()
    while s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
    s = re.sub(r"::[a-zA-Z_ ]+(\[\])?", "", s)
    s = s.replace('"', "")
    # PostgreSQL commonly adds harmless grouping parentheses when it stores a
    # CHECK expression. Canonical factgraph checks do not depend on explicit
    # parenthesis identity, so remove them for structural comparison.
    s = s.replace("(", "").replace(")", "")
    return re.sub(r"\s+", " ", s).strip().lower()


def expected_snapshot(model: Model) -> dict[str, Any]:
    plan = postgres.build_plan(model)
    tables: dict[str, Any] = {}
    for t in plan.tables:
        tables[t.name] = {
            "columns": [
                {"name": c.name, "type": c.sql_type.upper(), "nullable": bool(c.nullable)}
                for c in t.columns
            ],
            "primary_key": list(t.primary_key),
            "uniques": sorted([list(u) for u in t.uniques]),
            "checks": sorted(_normalize_check(c) for c in t.checks),
            "foreign_keys": sorted(
                [
                    {
                        "columns": list(f.columns),
                        "target_table": f.target_table,
                        "target_columns": list(f.target_columns),
                    }
                    for f in t.foreign_keys
                ],
                key=lambda x: (x["columns"], x["target_table"], x["target_columns"]),
            ),
        }
    return {"tables": tables}


def live_snapshot(conn, schema: str) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, is_nullable, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (schema,),
        )
        for table, col, nullable, dtype, udt in cur.fetchall():
            tables.setdefault(table, {"columns": [], "primary_key": [], "uniques": [], "checks": [], "foreign_keys": []})
            tables[table]["columns"].append({
                "name": col,
                "type": _type_name(dtype, udt),
                "nullable": nullable == "YES",
            })

        cur.execute(
            """
            SELECT c.relname AS table_name,
                   con.contype,
                   ARRAY(
                     SELECT a.attname
                     FROM unnest(con.conkey) WITH ORDINALITY k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
                     ORDER BY k.ord
                   ) AS source_columns,
                   tc.relname AS target_table,
                   CASE WHEN con.confkey IS NULL THEN ARRAY[]::text[] ELSE ARRAY(
                     SELECT a.attname
                     FROM unnest(con.confkey) WITH ORDINALITY k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum
                     ORDER BY k.ord
                   ) END AS target_columns,
                   pg_get_constraintdef(con.oid, true) AS definition
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_class tc ON tc.oid = con.confrelid
            WHERE n.nspname = %s
            ORDER BY c.relname, con.contype, con.oid
            """,
            (schema,),
        )
        for table, kind, source_cols, target_table, target_cols, definition in cur.fetchall():
            row = tables.setdefault(table, {"columns": [], "primary_key": [], "uniques": [], "checks": [], "foreign_keys": []})
            source_cols = list(source_cols or [])
            target_cols = list(target_cols or [])
            if kind == "p":
                row["primary_key"] = source_cols
            elif kind == "u":
                row["uniques"].append(source_cols)
            elif kind == "f":
                row["foreign_keys"].append({"columns": source_cols, "target_table": target_table, "target_columns": target_cols})
            elif kind == "c":
                row["checks"].append(_normalize_check(definition))
    for row in tables.values():
        row["uniques"] = sorted(row["uniques"])
        row["checks"] = sorted(row["checks"])
        row["foreign_keys"] = sorted(row["foreign_keys"], key=lambda x: (x["columns"], x["target_table"], x["target_columns"]))
    return {"tables": tables}


def compare_snapshot(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    exp, act = expected["tables"], actual["tables"]
    mismatches: list[dict[str, Any]] = []
    if sorted(exp) != sorted(act):
        mismatches.append({"kind": "table_names", "expected": sorted(exp), "actual": sorted(act)})
    for name in sorted(set(exp) & set(act)):
        for key in ["columns", "primary_key", "uniques", "checks", "foreign_keys"]:
            if exp[name][key] != act[name][key]:
                mismatches.append({"kind": key, "table": name, "expected": exp[name][key], "actual": act[name][key]})
    return {"passed": not mismatches, "mismatches": mismatches, "expected": expected, "actual": actual}


def _fetch_preflight(cur, sql: str) -> tuple[list[Any], list[str]]:
    rows: list[Any] = []
    executed: list[str] = []
    for stmt in _split_sql(sql):
        if stmt.lstrip().startswith("--"):
            continue
        cur.execute(stmt)
        executed.append(stmt)
        if cur.description:
            rows = cur.fetchall()
    return rows, executed


def evaluate_preflight(op: TargetMigrationOperation, rows: list[Any], executed: list[str]) -> dict[str, Any]:
    if op.safety == ChangeSafety.DESTRUCTIVE:
        return {"passed": True, "kind": "impact_only", "rows": rows, "executed": executed}
    if not executed:
        return {"passed": False, "kind": "missing_executable_preflight", "rows": rows, "executed": executed}
    if op.kind == "add_unique":
        passed = len(rows) == 0
    elif op.kind in {"set_not_null", "set_not_null_after_backfill", "add_check", "add_foreign_key"}:
        passed = bool(rows) and rows[0][0] == 0
    else:
        if not rows:
            passed = True
        elif len(rows) == 1 and len(rows[0]) == 1 and isinstance(rows[0][0], int):
            passed = rows[0][0] == 0
        else:
            passed = len(rows) == 0
    return {"passed": passed, "kind": "gate", "rows": rows, "executed": executed}


def run(
    before: Model,
    after: Model,
    dsn: str,
    *,
    hints: MigrationHints | None = None,
    fixture_path=None,
    policy: MigrationExecutionPolicy | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    policy = policy or MigrationExecutionPolicy()
    hints = hints or MigrationHints.empty()
    fixture = load_fixture(fixture_path)
    try:
        import psycopg  # type: ignore
    except ImportError:
        return {"target": "postgres", "status": "unavailable", "reason": "psycopg is not installed; install factgraph[conformance]", "operations": []}

    seed = hashlib.sha256(f"{before.id}|{after.id}".encode()).hexdigest()[:12]
    schema = f"fg_mig_{seed}"
    plan = pg_migration.build_plan(before, after, hints)
    operation_rows = [initial_operation_result(op, policy) for op in plan.operations]
    expected_complete = plan_expected_complete(plan, policy)

    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception as exc:
        return {"target": "postgres", "status": "unavailable", "reason": f"connection failed: {type(exc).__name__}: {exc}", "operations": operation_rows}

    report: dict[str, Any] = {
        "format": "factgraph-live-migration-report-v1",
        "target": "postgres",
        "before_model": before.name,
        "after_model": after.name,
        "namespace": schema,
        "transactional": True,
        "policy": policy.to_dict(),
        "fixture": str(fixture_path) if fixture_path else None,
        "expected_complete": expected_complete,
        "operations": operation_rows,
        "status": "running",
    }

    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute("SELECT version()")
            report["server"] = cur.fetchone()[0]
            for stmt in _split_sql(postgres.emit_sql(before)):
                cur.execute(stmt)
            for stmt in fixture.get("postgres", {}).get("setup_sql", []):
                cur.execute(str(stmt).rstrip(";"))
        report["before_verification"] = compare_snapshot(expected_snapshot(before), live_snapshot(conn, schema))

        # Migrations execute transactionally. A gated failure rolls every staged
        # schema change back to the before state.
        conn.autocommit = False
        failure: dict[str, Any] | None = None
        try:
            with conn.cursor() as cur:
                cur.execute(f'SET search_path TO "{schema}"')
                for op, row in zip(plan.operations, operation_rows):
                    if not operation_selected(op, policy):
                        continue
                    if op.preflight and op.safety in {ChangeSafety.REQUIRES_DATA_CHECK, ChangeSafety.DESTRUCTIVE}:
                        try:
                            rows, executed = _fetch_preflight(cur, op.preflight)
                            pf = evaluate_preflight(op, rows, executed)
                        except Exception as exc:
                            pf = {"passed": False, "kind": "preflight_error", "error": f"{type(exc).__name__}: {exc}"}
                        row["preflight"] = pf
                        if op.safety == ChangeSafety.REQUIRES_DATA_CHECK and not pf.get("passed"):
                            row["status"] = "preflight_failed"
                            failure = {"operation_id": op.id, "stage": "preflight", "detail": pf}
                            raise RuntimeError("factgraph gated preflight failed")
                    try:
                        for stmt in _split_sql(op.command or ""):
                            cur.execute(stmt)
                        row["status"] = "executed"
                        row["execution"] = {"passed": True}
                    except Exception as exc:
                        row["status"] = "execution_failed"
                        row["execution"] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
                        failure = {"operation_id": op.id, "stage": "execution", "detail": row["execution"]}
                        raise
            conn.commit()
        except Exception:
            conn.rollback()
            report["failure"] = failure
            report["status"] = "preflight_failed" if failure and failure.get("stage") == "preflight" else "execution_failed"
        finally:
            conn.autocommit = True

        actual = live_snapshot(conn, schema)
        expected_model = after if report["status"] == "running" else before
        report["verification"] = compare_snapshot(expected_snapshot(expected_model), actual)
        report["verification"]["compared_to"] = expected_model.name
        if report["status"] == "running":
            if expected_complete:
                report["status"] = "completed" if report["verification"]["passed"] else "verification_failed"
            else:
                report["status"] = "completed_partial"
                report["after_projection_verification"] = compare_snapshot(expected_snapshot(after), actual)
        return report
    finally:
        if not keep:
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            except Exception:
                pass
        conn.close()
