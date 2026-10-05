# v0.3 semantic contract

This document describes the normalized conceptual semantics. Surface syntax may be more convenient, but target adapters consume the normalized model rather than assigning semantics directly to syntax.

## Semantic identity versus names

Names are human-facing labels. By default Factgraph preserves the historical behavior of deriving deterministic semantic IDs from those names. Such IDs are reproducible but **not rename-stable**.

v0.8 adds an optional explicit identity token:

```text
model TeamApp identity "team-app" {
  value UserId: UUID identity "user-id"

  entity User identity "user" {
    id userId: UserId identity "user.id"
  }

  fact Membership identity "membership"(
    member: User identity "membership.member",
    team: Team identity "membership.team"
  )
}
```

The normalized IDs use a reserved `uid:` namespace derived from the identity token rather than the display name. Renaming `User` to `Person` while retaining `identity "user"` therefore preserves semantic identity.

Identity tokens are opt-in so old source remains valid. Every normal build emits `identity_coverage.json` showing which normalized elements use explicit rename-stable identity and which still use legacy name-derived identity. The compiler never uses fuzzy name similarity to infer identity.

Field sugar receives its own identity token so the generated field fact and owner/value roles remain aligned across field renames. Explicitly identified fact types likewise permit independently named roles to retain identity.


## Object types

`entity` declares an identity-bearing object type. `value` declares a value type backed by one of the built-in scalar kinds:

`String`, `Int`, `Decimal`, `Date`, `UUID`, `Bool`, `Timestamp`, `Float`.

## Value constraints

A value type may constrain its domain.

Numeric range:

```text
value Age: Int {
  range(0, 130)
}
```

Enumeration:

```text
value Status: String {
  oneof("active", "paused", "closed")
}
```

`range` is currently defined for `Int`, `Decimal`, and `Float`. `oneof` is defined for scalar value types; literals must match the scalar kind, Date/Timestamp/UUID literals are lexically validated, and duplicate enumeration values are rejected. These are conceptual value-domain constraints and are checked against sample facts whenever the constrained value type appears directly in a sampled role.

## Entity subtyping

v0.3 supports single-inheritance entity subtyping:

```text
subtype Employee is Person
```

Semantics:

```text
Employee^I ⊆ Person^I
```

The subtype inherits the supertype's preferred identifier and may not redeclare identifier fields in v0.3. Subtype cycles are invalid. Multiple direct supertypes are not supported yet.

Subtyping is also considered when checking role-sequence compatibility: a role played by `Employee` may project into a corresponding role played by `Person` where subset semantics permit it.

## Fact types

A fact type is an n-ary relation with named, typed roles:

```text
fact Recommendation(recommender: Person, candidate: Person, job: Job) { ... }
```

Conceptually, a fact population is a **set of tuples**, not a bag of event occurrences.

## Roles

Roles are first-class semantic objects. Repeated player types are not merged. In the recommendation example, `recommender` and `candidate` are two distinct roles even though both are played by `Person`.

## Fields

Field syntax is surface sugar. An entity field:

```text
entity Person { email: Email }
```

normalizes to a binary fact like `Person__email(owner: Person, value: Email)` plus uniqueness on `owner` and mandatory participation when the field is required.

`?` means optional participation at the conceptual level; it is not defined as “SQL NULL” in the semantic kernel.

## Identifiers

Fields prefixed with `id` are components of the entity's preferred identifier. Multiple `id` fields form a compound identifier.

A subtype inherits its supertype's identifier in v0.3.

## Objectification and relationship fields

A relationship can be explicitly objectified:

```text
fact Employment(employee: Person, employer: Company) objectify EmploymentRecord {
  salary: Money
}
```

If relationship fields exist without an explicit name, normalization creates `<FactName>Record` automatically. Relationship fields then normalize to facts about the objectified fact instance.

## Uniqueness

All fact populations have set semantics, so a duplicate complete role tuple is the same fact. `unique(a, b)` additionally constrains a role subset.

## Mandatory participation

`mandatory(role)` is a conceptual total-participation constraint. A target may or may not be able to enforce it. Field-origin mandatory constraints can usually become target-local `NOT NULL` / `required` rules.

An incomplete sample population is **not** treated as proof that a mandatory constraint is violated because samples are examples, not a closed-world universe of all entity instances.

## Frequency constraints

A frequency constraint limits how many fact tuples may share a projection onto selected roles:

```text
frequency(member, 0, 1)
frequency(mentor, 1, 2)
```

For a role sequence `R` and bounds `[min,max]`, every projected tuple that appears in the fact population must occur within those bounds.

Important: `frequency(..., min, max)` is **not total participation**. A `min` bound does not assert that every possible player appears in the fact population. `mandatory(role)` is the separate total-participation concept.

`max = 1` is equivalent to uniqueness on the selected role sequence and can therefore be mapped directly to common database uniqueness mechanisms. Higher bounds remain conceptual in the current canonical targets.

## Cross-fact role-sequence constraints

Role sequences are written as a fact name followed by selected roles:

```text
Approved(person, team)
Membership(person, team)
```

### Subset

```text
subset Approved(person, team) Membership(person, team)
```

requires:

```text
π(person,team)(Approved^I) ⊆ π(person,team)(Membership^I)
```

### Equality

```text
equality Current(person) Listed(person)
```

requires both role-sequence projections to contain exactly the same tuples.

### Exclusion

```text
exclusion Approved(person, team) Banned(person, team)
```

requires the two projections to be disjoint.

Role sequences must have the same arity and compatible player types. Subtype-to-supertype compatibility is accepted for subset direction and for exclusion overlap typing; equality currently requires exact player types.

These constraints are validated against sample populations in v0.3. The canonical database mappings retain them in capability/semantic artifacts but do not yet enforce them across fact tables/collections.

## Unordered role groups

```text
unordered(a, b)
```

means role positions are quotient-equivalent under permutation for fact identity. Thus `(Alice, Bob)` and `(Bob, Alice)` are one fact for that role group.

This is intentionally **not** the same as logical symmetry.

## Symmetric ring constraint

```text
symmetric
```

on a binary same-player fact means the predicate is logically symmetric: if `R(a,b)` holds then `R(b,a)` holds. It does not identify the two tuples as one stored fact.

## Readings

A canonical reading may be attached to a source fact. Every placeholder must name a role.

## Sample facts

`sample` declarations are validation examples, not a live database population. v0.3 checks:

- arity;
- set semantics;
- unordered canonicalization;
- uniqueness;
- direct value-domain constraints;
- frequency constraints over the sampled fact population;
- subset/equality/exclusion projections.

It deliberately does not infer failure of total participation or logical symmetry from an incomplete sample. It also does not currently define a separate closed sample population of entity instances from which subtype completeness could be checked.

## Model-shape analyses

`analysis` is separate from domain constraints.

Implemented:

- `analysis uniform(n)`
- `analysis connected`
- `analysis directed` → deliberately `unevaluated`; fact roles already encode orientation and no separate directedness semantics is defined.
