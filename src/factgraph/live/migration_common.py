from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from ..diff import ChangeSafety
from ..migrations.base import TargetMigrationOperation, TargetMigrationPlan


@dataclass(frozen=True)
class MigrationExecutionPolicy:
    """Explicit policy for a live migration run.

    Safe automatic operations are always eligible. Data-check-gated and
    destructive operations require separate opt-ins. Manual operations are
    never made executable by the generic harness.
    """

    allow_risky: bool = False
    allow_destructive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def operation_selected(op: TargetMigrationOperation, policy: MigrationExecutionPolicy) -> bool:
    if op.safety == ChangeSafety.MANUAL:
        return False
    if op.safety == ChangeSafety.DESTRUCTIVE:
        return policy.allow_destructive and bool(op.command)
    if op.safety == ChangeSafety.REQUIRES_DATA_CHECK:
        return policy.allow_risky and bool(op.command)
    return bool(op.automatic and op.command)


def plan_expected_complete(plan: TargetMigrationPlan, policy: MigrationExecutionPolicy) -> bool:
    """Whether policy can execute every physical operation in this plan."""
    return all(operation_selected(op, policy) for op in plan.operations)


def load_fixture(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"format": "factgraph-live-migration-fixture-v1", "postgres": {}, "mongo": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("format") != "factgraph-live-migration-fixture-v1":
        raise ValueError(f"unsupported migration fixture format in {path}")
    if not isinstance(value.get("postgres", {}), dict) or not isinstance(value.get("mongo", {}), dict):
        raise ValueError("migration fixture postgres/mongo sections must be objects")
    return value


def initial_operation_result(op: TargetMigrationOperation, policy: MigrationExecutionPolicy) -> dict[str, Any]:
    selected = operation_selected(op, policy)
    if op.safety == ChangeSafety.MANUAL:
        reason = "manual operations are never executed by the generic harness"
    elif op.safety == ChangeSafety.REQUIRES_DATA_CHECK and not policy.allow_risky:
        reason = "requires --allow-risky"
    elif op.safety == ChangeSafety.DESTRUCTIVE and not policy.allow_destructive:
        reason = "requires --allow-destructive"
    elif not op.command:
        reason = "no executable command available"
    else:
        reason = None
    return {
        "operation_id": op.id,
        "kind": op.kind,
        "phase": op.phase,
        "safety": op.safety.value,
        "description": op.description,
        "selected": selected,
        "status": "pending" if selected else "blocked",
        "blocked_reason": reason,
        "preflight": None,
        "execution": None,
    }


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    operations = report.get("operations", [])
    return {
        "target": report.get("target"),
        "status": report.get("status"),
        "operation_count": len(operations),
        "executed_count": sum(x.get("status") == "executed" for x in operations),
        "blocked_count": sum(x.get("status") == "blocked" for x in operations),
        "preflight_failed_count": sum(x.get("status") == "preflight_failed" for x in operations),
        "execution_failed_count": sum(x.get("status") == "execution_failed" for x in operations),
        "verification_passed": report.get("verification", {}).get("passed"),
        "expected_complete": report.get("expected_complete"),
    }
