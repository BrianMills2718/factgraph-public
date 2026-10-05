"""Referential-integrity guard for every model factory that refers to another element.

Defect class (ChatGPT re-review, chat 6abb8160): constraint factories recomputed
``stable_id("fact", name)`` and name-derived role ids instead of using the ids
of the elements they refer to.  With an explicit identity
(``FactType.create(..., identity="custom")``) those recomputed ids do not exist.

Every factory that produces a cross-reference must take the referenced element
(or its real id).  ``REFERENCE_FACTORIES`` below is checked against the
factories actually defined on the model classes, so a new factory cannot be
added without being exercised here.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Callable

import pytest

from factgraph.audit import build_audit
from factgraph.model import (
    Constraint,
    ConstraintKind,
    EntityType,
    FactType,
    FieldProjectionHint,
    Model,
    ObjectifiedFactType,
    Reading,
    ValueType,
)
from factgraph.validate import validate_model


@dataclass
class Fixture:
    model: Model
    person: EntityType
    employee: EntityType
    company: EntityType
    age: ValueType
    works: FactType  # binary, both roles play Person/Company
    knows: FactType  # binary, same player (ring/unordered)
    employs: FactType  # binary, same shape as works (role sets)
    field_fact: FactType


def _build(shape: str) -> Fixture:
    """Build a model whose elements use the requested identity shape.

    explicit: every element carries an explicit identity.
    mixed:    explicit fact ids with name-derived role ids (and vice versa).
    legacy:   historical name-derived ids everywhere.
    """

    def ident(token: str) -> str | None:
        return None if shape == "legacy" else token

    model = Model.create("RefModel", identity=ident("model-1"))
    person = EntityType.create("Person", identity=ident("person-uid"))
    employee = EntityType.create("Employee", identity=ident("employee-uid"))
    company = EntityType.create("Company", identity=ident("company-uid"))
    age = ValueType.create("Age", "Int", identity=ident("age-uid"))
    for obj in (person, employee, company, age):
        model.object_types[obj.id] = obj

    if shape == "mixed":
        works = FactType.create("Works", [("emp", person.id), ("co", company.id)], identity="works-uid")
        knows = FactType.create("Knows", [("a", person.id, "knows-a"), ("b", person.id, "knows-b")])
        employs = FactType.create("Employs", [("emp", person.id), ("co", company.id, "employs-co")], identity="employs-uid")
    else:
        works = FactType.create(
            "Works", [("emp", person.id, ident("works-emp")), ("co", company.id, ident("works-co"))], identity=ident("works-uid")
        )
        knows = FactType.create(
            "Knows", [("a", person.id, ident("knows-a")), ("b", person.id, ident("knows-b"))], identity=ident("knows-uid")
        )
        employs = FactType.create(
            "Employs", [("emp", person.id, ident("employs-emp")), ("co", company.id, ident("employs-co"))], identity=ident("employs-uid")
        )
    field_fact = FactType.create(
        "Person__age",
        [("owner", person.id, ident("age-field/owner")), ("value", age.id, ident("age-field/value"))],
        identity=ident("age-field"),
    )
    for fact in (works, knows, employs, field_fact):
        model.fact_types[fact.id] = fact
    model.field_hints[field_fact.id] = FieldProjectionHint(person.id, field_fact.id, "age", age.id, True, True)
    return Fixture(model, person, employee, company, age, works, knows, employs, field_fact)


# Every factory on the model that produces an element referring to another
# element.  Each entry returns (element, attribute-name -> expected real ids).
FactoryCase = Callable[[Fixture], tuple[object, dict[str, object]]]

REFERENCE_FACTORIES: dict[str, FactoryCase] = {
    "Constraint.uniqueness": lambda f: (
        Constraint.uniqueness(f.works, ["emp"]),
        {"fact_type_id": f.works.id, "role_ids": (f.works.role("emp").id,)},
    ),
    "Constraint.mandatory": lambda f: (
        Constraint.mandatory(f.works, "emp"),
        {"fact_type_id": f.works.id, "role_ids": (f.works.role("emp").id,)},
    ),
    "Constraint.frequency": lambda f: (
        Constraint.frequency(f.works, ["co"], 1, 3),
        {"fact_type_id": f.works.id, "role_ids": (f.works.role("co").id,)},
    ),
    "Constraint.unordered": lambda f: (
        Constraint.unordered(f.knows, ["a", "b"]),
        {"fact_type_id": f.knows.id, "role_ids": (f.knows.role("a").id, f.knows.role("b").id)},
    ),
    "Constraint.ring": lambda f: (
        Constraint.ring(f.knows, "symmetric"),
        {"fact_type_id": f.knows.id},
    ),
    "Constraint.role_set": lambda f: (
        Constraint.role_set(ConstraintKind.SUBSET, f.works, ["emp", "co"], f.employs, ["emp", "co"]),
        {
            "fact_type_id": f.works.id,
            "role_ids": (f.works.role("emp").id, f.works.role("co").id),
            "target_fact_type_id": f.employs.id,
            "target_role_ids": (f.employs.role("emp").id, f.employs.role("co").id),
        },
    ),
    "Constraint.preferred_identifier": lambda f: (
        Constraint.preferred_identifier(f.person.id, [f.field_fact.id]),
        {"object_type_id": f.person.id, "field_fact_ids": (f.field_fact.id,)},
    ),
    "Constraint.value": lambda f: (
        Constraint.value(f.age.id, {"kind": "range", "min": 0, "max": 150}),
        {"object_type_id": f.age.id},
    ),
    "Constraint.subtype": lambda f: (
        Constraint.subtype(f.employee.id, f.person.id),
        {"subtype_id": f.employee.id, "supertype_id": f.person.id},
    ),
    "Reading.create": lambda f: (
        Reading.create(f.works, "{emp} works for {co}", (f.works.role("emp").id, f.works.role("co").id)),
        {"fact_type_id": f.works.id, "role_ids": (f.works.role("emp").id, f.works.role("co").id)},
    ),
    "ObjectifiedFactType.create": lambda f: (
        ObjectifiedFactType.create("Employment", fact_type_id=f.works.id, identity=None),
        {"fact_type_id": f.works.id},
    ),
}

# Factories that only mint an element's own id (no reference to another element).
SELF_ONLY_FACTORIES = {
    "EntityType.create",
    "ValueType.create",
    "FactType.create",  # roles are built with the fact's own resolved id
    "Role.create",  # only called by FactType.create with the fact's real id
    "Model.create",
}

SHAPES = ["explicit", "mixed", "legacy"]


def _defined_factories() -> set[str]:
    out: set[str] = set()
    for cls in (Constraint, Reading, ObjectifiedFactType, EntityType, ValueType, FactType, Model):
        for name, member in vars(cls).items():
            if isinstance(member, staticmethod) and not name.startswith("_"):
                if name.startswith("from_"):
                    continue  # deserializers copy ids verbatim
                out.add(f"{cls.__name__}.{name}")
    from factgraph.model import Role

    for name, member in vars(Role).items():
        if isinstance(member, staticmethod):
            out.add(f"Role.{name}")
    return out


def test_every_factory_is_covered_by_the_reference_guard():
    defined = _defined_factories()
    covered = set(REFERENCE_FACTORIES) | SELF_ONLY_FACTORIES
    assert defined - covered == set(), "new model factory must be added to REFERENCE_FACTORIES"
    assert covered - defined == set(), "stale entry in the reference guard"


def test_every_constraint_kind_has_a_factory_case():
    built = {REFERENCE_FACTORIES[k](_build("explicit"))[0].kind for k in REFERENCE_FACTORIES if k.startswith("Constraint.")}
    # role_set covers SUBSET here; EQUALITY/EXCLUSION share its code path.
    assert set(ConstraintKind) - built == {ConstraintKind.EQUALITY, ConstraintKind.EXCLUSION}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("factory", sorted(REFERENCE_FACTORIES))
def test_factory_references_resolve_to_real_element_ids(factory: str, shape: str):
    fx = _build(shape)
    element, expected = REFERENCE_FACTORIES[factory](fx)
    for attr, value in expected.items():
        assert getattr(element, attr) == value, f"{factory} [{shape}] {attr}"
    if isinstance(element, Constraint):
        fx.model.constraints[element.id] = element
    elif isinstance(element, Reading):
        fx.model.readings[element.id] = element
    else:
        fx.model.object_types[element.id] = element
    assert fx.model.dangling_references() == []
    assert [d for d in validate_model(fx.model) if d.severity.value == "error"] == []


def test_finding_repro_explicit_fact_identity_uniqueness():
    """The exact ChatGPT trigger: explicit fact identity, then a constraint on it."""
    fx = _build("explicit")
    c = Constraint.uniqueness(fx.works, ["emp"])
    assert c.fact_type_id == "uid:fact:works-uid"
    assert c.role_ids == ("uid:role:works-emp",)


def test_factories_reject_names_instead_of_elements():
    with pytest.raises(TypeError):
        Constraint.uniqueness("Works", ["emp"])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Reading.create("Works", "{emp}", ())  # type: ignore[arg-type]


def test_factories_reject_unknown_role_names():
    fx = _build("explicit")
    with pytest.raises(KeyError):
        Constraint.mandatory(fx.works, "nope")
    with pytest.raises(ValueError):
        Reading.create(fx.works, "{emp}", ("role:Works:emp",))


def test_legacy_constraint_ids_are_unchanged():
    """Name-derived models keep their historical manifest ids."""
    fx = _build("legacy")
    assert Constraint.uniqueness(fx.works, ["emp"]).id == "constraint:unique:Works:emp"
    assert Constraint.mandatory(fx.works, "emp").id == "constraint:mandatory:Works:emp"
    assert Constraint.ring(fx.knows, "symmetric").id == "constraint:ring:symmetric:Knows"
    assert (
        Constraint.role_set(ConstraintKind.SUBSET, fx.works, ["emp"], fx.employs, ["emp"]).id
        == "constraint:subset:Works:emp:to:Employs:emp"
    )
    assert Reading.create(fx.works, "{emp}", (fx.works.role("emp").id,)).id == "reading:Works:canonical"


def _dangling_model() -> Model:
    fx = _build("explicit")
    # What the old name-recomputing factories produced for an explicit fact.
    bad = Constraint("constraint:bad", ConstraintKind.UNIQUENESS, "fact:Works", ("role:Works:emp",))
    fx.model.constraints[bad.id] = bad
    fx.model.readings["reading:bad"] = Reading("reading:bad", fx.works.id, "{emp}", ("role:Works:emp",))
    return fx.model


def test_integrity_check_reports_dangling_constraint_and_reading():
    model = _dangling_model()
    refs = model.dangling_references()
    ids = {r.element_id for r in refs}
    assert {"constraint:bad", "reading:bad"} <= ids
    codes = {d.code for d in validate_model(model) if d.severity.value == "error"}
    assert "CONSTRAINT_UNKNOWN_FACT" in codes
    assert "READING_UNKNOWN_ROLE_ID" in codes


def test_validate_does_not_crash_on_dangling_set_constraint():
    fx = _build("explicit")
    bad = Constraint(
        "constraint:bad-subset",
        ConstraintKind.SUBSET,
        fact_type_id="fact:Works",
        role_ids=("role:Works:emp",),
        target_fact_type_id=fx.employs.id,
        target_role_ids=(fx.employs.role("emp").id,),
    )
    fx.model.constraints[bad.id] = bad
    codes = {d.code for d in validate_model(fx.model)}
    assert "CONSTRAINT_UNKNOWN_FACT" in codes


def test_audit_fails_loudly_on_dangling_references():
    with pytest.raises(ValueError, match="dangling"):
        build_audit(_dangling_model())
