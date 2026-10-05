from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
import json


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    element_id: str | None = None
    source_line: int | None = None


class EvaluationState(str, Enum):
    PASSED = "passed"
    VIOLATED = "violated"
    UNEVALUATED = "unevaluated"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class AnalysisResult:
    name: str
    state: EvaluationState
    message: str


class CapabilityStatus(str, Enum):
    NATIVE_ENFORCED = "native_enforced"
    EMULATED_ENFORCED = "emulated_enforced"
    REPRESENTED_NOT_ENFORCED = "represented_not_enforced"
    METADATA_ONLY = "metadata_only"
    UNSUPPORTED = "unsupported"
    LOSSY_DROPPED = "lossy_dropped"


@dataclass(frozen=True)
class CapabilityEntry:
    feature: str
    source_element: str
    target: str
    status: CapabilityStatus
    mechanism: str | None = None
    reason: str | None = None


@dataclass
class CapabilityReport:
    target: str
    entries: list[CapabilityEntry]

    @property
    def has_loss(self) -> bool:
        return any(e.status in {CapabilityStatus.UNSUPPORTED, CapabilityStatus.LOSSY_DROPPED} for e in self.entries)

    def to_dict(self) -> dict:
        return {
            "target": self.target,
            "has_loss": self.has_loss,
            "entries": [
                {**asdict(e), "status": e.status.value}
                for e in sorted(self.entries, key=lambda x: (x.source_element, x.feature, x.status.value))
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def diagnostics_json(items: list[Diagnostic]) -> str:
    return json.dumps(
        [{**asdict(d), "severity": d.severity.value} for d in items],
        indent=2,
        sort_keys=True,
    ) + "\n"


def analyses_json(items: list[AnalysisResult]) -> str:
    return json.dumps(
        [{**asdict(a), "state": a.state.value} for a in items],
        indent=2,
        sort_keys=True,
    ) + "\n"
