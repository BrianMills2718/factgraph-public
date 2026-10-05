from __future__ import annotations

from typing import Any

from .ids import explicit_token
from .model import EntityType, Model, ObjectifiedFactType, ValueType


def identity_report(model: Model) -> dict[str, Any]:
    """Report which source-level semantic elements have rename-stable IDs.

    Legacy name-derived identifiers remain valid for backwards compatibility,
    but a repository branch/merge workflow can only distinguish a rename from a
    delete+add without hints when the relevant element carries an explicit
    identity token.
    """

    items: list[dict[str, Any]] = []

    def add(kind: str, name: str, element_id: str, owner: str | None = None) -> None:
        token = explicit_token(kind, element_id)
        items.append(
            {
                "kind": kind,
                "name": name,
                "owner": owner,
                "id": element_id,
                "stable_across_rename": token is not None,
                "identity_token": token,
            }
        )

    add("model", model.name, model.id)
    for obj in sorted(model.object_types.values(), key=lambda x: (type(x).__name__, x.name, x.id)):
        if isinstance(obj, EntityType):
            add("entity", obj.name, obj.id)
        elif isinstance(obj, ValueType):
            add("value", obj.name, obj.id)
        elif isinstance(obj, ObjectifiedFactType):
            add("objectified", obj.name, obj.id)

    field_fact_ids = set(model.field_hints)
    for fact in sorted(model.fact_types.values(), key=lambda x: (x.name, x.id)):
        if fact.id in field_fact_ids:
            hint = model.field_hints[fact.id]
            owner = model.object_types[hint.owner_object_type_id].name
            add("fact", hint.field_name, fact.id, owner=owner)
            continue
        add("fact", fact.name, fact.id)
        for role in sorted(fact.roles, key=lambda r: r.ordinal):
            add("role", role.name, role.id, owner=fact.name)

    stable = sum(1 for x in items if x["stable_across_rename"])
    return {
        "format": "factgraph-identity-coverage-v1",
        "model": model.name,
        "total": len(items),
        "stable": stable,
        "legacy_name_derived": len(items) - stable,
        "coverage": 1.0 if not items else stable / len(items),
        "all_stable": stable == len(items),
        "items": items,
        "note": (
            "Legacy name-derived identities are valid but renames may require migration hints. "
            "Explicit identity tokens are semantic IDs and survive display-name changes."
        ),
    }
