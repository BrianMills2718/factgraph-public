from __future__ import annotations

from dataclasses import dataclass

from .model import Model


@dataclass(frozen=True)
class IncidenceIndex:
    roles_by_object: dict[str, tuple[str, ...]]
    roles_by_fact: dict[str, tuple[str, ...]]

    @staticmethod
    def build(model: Model) -> "IncidenceIndex":
        by_object: dict[str, list[str]] = {oid: [] for oid in model.object_types}
        by_fact: dict[str, list[str]] = {}
        for fact in model.fact_types.values():
            by_fact[fact.id] = []
            for role in sorted(fact.roles, key=lambda r: r.ordinal):
                by_fact[fact.id].append(role.id)
                by_object.setdefault(role.player_id, []).append(role.id)
        return IncidenceIndex(
            {k: tuple(sorted(v)) for k, v in sorted(by_object.items())},
            {k: tuple(v) for k, v in sorted(by_fact.items())},
        )
