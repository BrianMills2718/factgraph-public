from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
from typing import Any

from .diff import ChangeSafety, SemanticDiff


class MigrationPhase(str, Enum):
    PREFLIGHT = "preflight"
    SCHEMA = "schema"
    DATA = "data"
    CONSTRAINTS = "constraints"
    CLEANUP = "cleanup"
    METADATA = "metadata"


@dataclass(frozen=True)
class MigrationStep:
    id: str
    change_id: str
    phase: MigrationPhase
    safety: ChangeSafety
    automatic: bool
    description: str
    preconditions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["phase"] = self.phase.value
        d["safety"] = self.safety.value
        return d


@dataclass
class MigrationPlan:
    before_model: str
    after_model: str
    semantic_diff: SemanticDiff
    steps: list[MigrationStep]

    def to_dict(self) -> dict[str, Any]:
        by_safety = {s.value: 0 for s in ChangeSafety}
        for step in self.steps:
            by_safety[step.safety.value] += 1
        blocked = [s for s in self.steps if not s.automatic]
        return {
            "format": "factgraph-semantic-migration-plan-v1",
            "before_model": self.before_model,
            "after_model": self.after_model,
            "summary": {
                "step_count": len(self.steps),
                "automatic_step_count": len(self.steps) - len(blocked),
                "blocked_step_count": len(blocked),
                "by_safety": by_safety,
                "safe_to_apply_without_data_inspection": not any(
                    s.safety in {ChangeSafety.REQUIRES_DATA_CHECK, ChangeSafety.DESTRUCTIVE, ChangeSafety.MANUAL}
                    for s in self.steps
                ),
            },
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        d = self.to_dict()
        lines = [
            f"# Semantic migration plan — {self.before_model} → {self.after_model}",
            "",
            "This plan is target-independent. PostgreSQL/MongoDB files translate the same semantic changes into target-specific actions.",
            "",
            f"Steps: **{d['summary']['step_count']}**; blocked/manual: **{d['summary']['blocked_step_count']}**.",
            "",
            "| Phase | Safety | Automatic | Description |",
            "| --- | --- | --- | --- |",
        ]
        for s in self.steps:
            description = s.description.replace("|", "\\|")
            lines.append(
                f"| `{s.phase.value}` | `{s.safety.value}` | {'yes' if s.automatic else 'no'} | {description} |"
            )
        lines += ["", "## Preconditions and notes", ""]
        for s in self.steps:
            if not s.preconditions and not s.notes:
                continue
            lines.append(f"### {s.id}")
            lines.append("")
            for p in s.preconditions:
                lines.append(f"- Preconditions: {p}")
            for n in s.notes:
                lines.append(f"- Note: {n}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def plan_semantic_migration(diff: SemanticDiff) -> MigrationPlan:
    steps: list[MigrationStep] = []
    for idx, change in enumerate(diff.changes, start=1):
        if change.safety == ChangeSafety.SAFE:
            automatic = True
        else:
            automatic = False
        # Semantic planning is intentionally conservative. A target adapter may
        # implement some requires-data-check operations after emitting a preflight.
        phase = MigrationPhase.METADATA
        if any(token in change.kind.value for token in ("field", "fact", "object_type", "objectification", "role", "subtype")):
            phase = MigrationPhase.SCHEMA
        if "constraint" in change.kind.value or change.kind.value in {"add_subtype", "drop_subtype", "change_subtype"}:
            phase = MigrationPhase.CONSTRAINTS
        if change.kind.value.startswith("drop_") and change.safety == ChangeSafety.DESTRUCTIVE:
            phase = MigrationPhase.CLEANUP
        notes: list[str] = []
        if change.safety == ChangeSafety.REQUIRES_DATA_CHECK:
            notes.append("target adapters should emit a deterministic preflight where possible before making this change executable")
        elif change.safety == ChangeSafety.DESTRUCTIVE:
            notes.append("destructive target operations are previewed but not emitted as executable commands unless explicitly requested")
        elif change.safety == ChangeSafety.MANUAL:
            notes.append("the semantic compiler refuses to invent an instance-data conversion policy")
        steps.append(
            MigrationStep(
                id=f"semantic-step-{idx:03d}",
                change_id=change.id,
                phase=phase,
                safety=change.safety,
                automatic=automatic,
                description=change.summary,
                preconditions=change.preconditions,
                notes=tuple(notes),
            )
        )
    return MigrationPlan(diff.before_model, diff.after_model, diff, steps)
