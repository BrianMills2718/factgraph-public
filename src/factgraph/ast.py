from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldDecl:
    name: str
    type_name: str
    required: bool
    identifier: bool
    identity: str | None
    line: int


@dataclass(frozen=True)
class ValueConstraintDecl:
    kind: str
    args: tuple[Any, ...]
    line: int


@dataclass(frozen=True)
class EntityDecl:
    name: str
    fields: tuple[FieldDecl, ...]
    identity: str | None
    line: int


@dataclass(frozen=True)
class ValueDecl:
    name: str
    scalar_kind: str
    constraints: tuple[ValueConstraintDecl, ...]
    identity: str | None
    line: int


@dataclass(frozen=True)
class RoleDecl:
    name: str
    type_name: str
    identity: str | None
    line: int


@dataclass(frozen=True)
class UniqueDecl:
    role_names: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class MandatoryDecl:
    role_name: str
    line: int


@dataclass(frozen=True)
class FrequencyDecl:
    role_names: tuple[str, ...]
    min_frequency: int
    max_frequency: int
    line: int


@dataclass(frozen=True)
class UnorderedDecl:
    role_names: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class RingDecl:
    kind: str
    line: int


@dataclass(frozen=True)
class FactDecl:
    name: str
    roles: tuple[RoleDecl, ...]
    fields: tuple[FieldDecl, ...]
    reading: str | None
    uniques: tuple[UniqueDecl, ...]
    mandatories: tuple[MandatoryDecl, ...]
    frequencies: tuple[FrequencyDecl, ...]
    unordered: tuple[UnorderedDecl, ...]
    rings: tuple[RingDecl, ...]
    objectify_name: str | None
    identity: str | None
    objectify_identity: str | None
    line: int


@dataclass(frozen=True)
class RoleSequenceDecl:
    fact_name: str
    role_names: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class SetConstraintDecl:
    kind: str
    left: RoleSequenceDecl
    right: RoleSequenceDecl
    line: int


@dataclass(frozen=True)
class SubtypeDecl:
    subtype_name: str
    supertype_name: str
    line: int


@dataclass(frozen=True)
class SampleDecl:
    fact_name: str
    values: tuple[str, ...]
    line: int


@dataclass(frozen=True)
class AnalysisDecl:
    name: str
    args: tuple[str, ...]
    line: int


@dataclass
class ModelAst:
    name: str
    identity: str | None = None
    values: list[ValueDecl] = field(default_factory=list)
    entities: list[EntityDecl] = field(default_factory=list)
    facts: list[FactDecl] = field(default_factory=list)
    set_constraints: list[SetConstraintDecl] = field(default_factory=list)
    subtypes: list[SubtypeDecl] = field(default_factory=list)
    samples: list[SampleDecl] = field(default_factory=list)
    analyses: list[AnalysisDecl] = field(default_factory=list)
