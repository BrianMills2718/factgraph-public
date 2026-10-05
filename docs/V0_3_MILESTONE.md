# v0.3 milestone — richer fact-model semantics

## Goal

Extend the v0.2 executable-conformance compiler with a small but materially richer slice of ORM-style semantics while preserving the rule that every target claim is explicit and testable.

## Added semantic constructs

### Value domains

- numeric `range(min,max)`;
- scalar `oneof(...)` enumerations.

### Frequency

`frequency(role..., min, max)` constrains multiplicity of role-sequence projections within a fact population. `max=1` is targetable as uniqueness. Higher bounds remain semantic-only in the canonical targets.

### Cross-fact set constraints

- `subset A(...) B(...)`;
- `equality A(...) B(...)`;
- `exclusion A(...) B(...)`.

These operate on role-sequence projections, not on table names or backend columns.

### Entity subtyping

`subtype Child is Parent` establishes conceptual population inclusion. v0.3 supports one direct entity supertype and inherited identity.

## Target behavior

### PostgreSQL

- value domains become `CHECK` constraints;
- max-one frequency becomes `UNIQUE`;
- subtype inclusion uses table-per-type PK/FK linkage;
- higher frequency and cross-fact set constraints are retained/reported but not enforced.

### MongoDB

- JSON-safe value domains become validator keywords; Decimal/Date/Timestamp literal constraints remain explicit gaps in the pure JSON target spec;
- max-one frequency becomes a unique index;
- subtype documents reuse inherited identity shape, but cross-collection subtype inclusion is deliberately not claimed as enforced;
- higher frequency and cross-fact set constraints are retained/reported but not enforced.

### GraphQL

GraphQL SDL carries shape and semantic descriptions/metadata only for the new constraints; it does not claim persistence enforcement.

## Validation behavior

Sample populations now evaluate:

- value domains for directly sampled value roles;
- frequency projection counts;
- subset/equality/exclusion projection relations.

Samples remain open-world examples, so absent entity instances are not used to infer mandatory-participation or subtype-population violations.

## Explicit non-goals

v0.3 does not implement:

- arbitrary database reverse engineering;
- triggers/materialized helper tables for every cross-fact constraint;
- multiple inheritance;
- complete ORM constraint coverage;
- instance migration;
- query compilation.

## Release criterion

The release is acceptable only if:

1. prior v0.2 tests remain green;
2. new semantic round-trip tests pass;
3. every native/emulated PostgreSQL/MongoDB capability has a conformance case;
4. all examples build deterministically;
5. unsupported/non-enforced semantics remain explicit in capability artifacts;
6. live database checks are never reported as passed unless a real service executed them.
