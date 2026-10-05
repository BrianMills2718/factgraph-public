from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any

from ..diff import ChangeSafety


@dataclass(frozen=True)
class TargetMigrationOperation:
    id: str
    phase: str
    kind: str
    safety: ChangeSafety
    automatic: bool
    description: str
    target_object: str | None = None
    command: str | None = None
    preflight: str | None = None
    rollback: str | None = None
    notes: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["safety"] = self.safety.value
        return d


@dataclass
class TargetMigrationPlan:
    target: str
    before_model: str
    after_model: str
    operations: list[TargetMigrationOperation]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        by_safety = {s.value: 0 for s in ChangeSafety}
        for op in self.operations:
            by_safety[op.safety.value] += 1
        return {
            "format": "factgraph-target-migration-plan-v1",
            "target": self.target,
            "before_model": self.before_model,
            "after_model": self.after_model,
            "warnings": self.warnings,
            "summary": {
                "operation_count": len(self.operations),
                "automatic_count": sum(op.automatic for op in self.operations),
                "blocked_count": sum(not op.automatic for op in self.operations),
                "by_safety": by_safety,
                "has_destructive": by_safety[ChangeSafety.DESTRUCTIVE.value] > 0,
                "has_manual": by_safety[ChangeSafety.MANUAL.value] > 0,
                "has_data_checks": by_safety[ChangeSafety.REQUIRES_DATA_CHECK.value] > 0,
            },
            "operations": [op.to_dict() for op in self.operations],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        d = self.to_dict()
        lines = [
            f"# {self.target} migration plan — {self.before_model} → {self.after_model}",
            "",
            "| Phase | Safety | Automatic | Operation |",
            "| --- | --- | --- | --- |",
        ]
        for op in self.operations:
            description = op.description.replace("|", "\\|")
            lines.append(
                f"| `{op.phase}` | `{op.safety.value}` | {'yes' if op.automatic else 'no'} | {description} |"
            )
        if self.warnings:
            lines += ["", "## Warnings", ""]
            lines.extend(f"- {x}" for x in self.warnings)
        lines += ["", "## Operation details", ""]
        for op in self.operations:
            lines += [f"### {op.id}", "", op.description, ""]
            if op.preflight:
                lines += ["Preflight:", "```text", op.preflight, "```", ""]
            if op.command:
                lines += ["Command/preview:", "```text", op.command, "```", ""]
            if op.rollback:
                lines += ["Rollback hint:", "```text", op.rollback, "```", ""]
            for note in op.notes:
                lines.append(f"- {note}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
