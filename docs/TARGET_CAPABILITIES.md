# Target capability model

Each target emits a `capabilities.json` file. Constraint enforcement uses these states:

- `native_enforced` — target-native schema mechanisms enforce the relevant semantics;
- `emulated_enforced` — generated machinery enforces the semantics, but not as a direct native primitive;
- `represented_not_enforced` — meaning is retained in the semantic artifact/report but the generated target does not enforce it;
- `metadata_only` — retained only as compiler metadata;
- `unsupported` — adapter cannot represent the feature;
- `lossy_dropped` — forbidden by default; intended for future explicit lossy mode only.

## PostgreSQL

The canonical v0.3 projection supports:

- table/column scalar shape;
- primary and foreign keys;
- uniqueness;
- field nullability;
- numeric `range` and scalar `oneof` value constraints via `CHECK`;
- `frequency(..., max=1)` via `UNIQUE`;
- entity subtyping via table-per-type primary-key/foreign-key linkage;
- canonical-order checks for unordered role groups where applicable.

Currently represented but not database-enforced by the canonical mapping:

- total participation that requires checking outside the local row/table;
- frequency bounds greater than one;
- subset/equality/exclusion across fact projections;
- logical symmetric companion facts.

## MongoDB

The canonical v0.3 projection uses:

- `$jsonSchema` for document shape/types;
- `required` for local required fields;
- JSON-safe value-domain keywords such as `minimum`, `maximum`, and `enum`;
- unique indexes for uniqueness and `frequency(..., max=1)`;
- `$expr` for simple canonical-order checks;
- inherited identifier field shape for subtype collections.

For value domains, the canonical pure JSON spec claims native enforcement only when the needed BSON literals are JSON-safe: `Int`/`Float` ranges and `String`/`Int`/`Float`/`Bool`/`UUID` enumerations. Decimal128 and BSON Date/Timestamp constraint literals require an additional BSON/Extended-JSON representation layer, so those declarations are retained but explicitly reported `represented_not_enforced`.

MongoDB does **not** natively enforce that every subtype collection document has a corresponding supertype collection document in the canonical mapping. Subtyping therefore remains `represented_not_enforced` even though the inherited key shape is emitted.

Cross-collection total participation, higher frequency bounds, subset/equality/exclusion, and logical symmetric companion facts are also reported as not enforced.

## GraphQL

GraphQL is treated as an API/type-schema projection, not a persistence backend. SDL captures type shape and field nullability and may retain semantic notes/descriptions for subtyping and constraints.

SDL does not by itself enforce persistence uniqueness, foreign keys, value-domain checks in storage, frequency constraints, subset/equality/exclusion, total participation, unordered fact identity, subtype population inclusion, or ring constraints. Those features are therefore reported as metadata or represented-not-enforced rather than silently treated as enforced.

## Conformance coverage

In v0.3, PostgreSQL and MongoDB `native_enforced`/`emulated_enforced` entries are not merely descriptive. `conformance/coverage.json` requires each such capability source element to have a generated structural or runtime conformance case.

New v0.3 conformance cases include:

- accepted and rejected value-domain examples;
- accepted/rejected `max=1` frequency populations;
- PostgreSQL subtype FK enforcement;
- MongoDB subtype non-enforcement gap probe;
- structural gap probes for higher frequency bounds and cross-fact set constraints.

Selected `represented_not_enforced` entries receive explicit gap probes. See `EXECUTABLE_CONFORMANCE.md`.
