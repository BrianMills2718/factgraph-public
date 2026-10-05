# v0.14 — external PostgreSQL artifact audit

## Purpose

v0.14 is the first milestone in which Factgraph can test a PostgreSQL schema that **Factgraph did not generate**.

The source conceptual model remains the semantic oracle. Factgraph synthesizes the same source-level invalid population used by the portability benchmark, but a small explicit mapping manifest tells the auditor where the corresponding entity/fact tables, role columns, and field columns live in an externally produced PostgreSQL schema.

The mapping is explicit by design. Factgraph does not infer domain intent from table or column names.

## Workflow

```bash
factgraph postgres-map-template domain.fg \
  --schema external.sql \
  --out mapping.json

# edit the physical table/column names in mapping.json

factgraph audit-postgres-artifact domain.fg \
  --schema external.sql \
  --mapping mapping.json \
  --out-dir artifacts/external-pg
```

The generated-only run writes the mapped witness programs but remains `generated_not_run`.

A live run adds:

```bash
factgraph audit-postgres-artifact domain.fg \
  --schema external.sql \
  --mapping mapping.json \
  --postgres-dsn "$FACTGRAPH_POSTGRES_DSN" \
  --out-dir artifacts/external-pg-live
```

## Evidence contract

The manifest is bound to:

- the normalized source semantic model SHA-256;
- the exact external DDL SHA-256;
- the explicit semantic-to-physical mapping.

Before any semantic witness can count as live evidence, the PostgreSQL runner:

1. creates an isolated schema;
2. applies the external DDL;
3. verifies that every mapped physical table and column exists;
4. destroys the preflight schema;
5. executes each source-semantic witness in a fresh isolated schema.

A DDL failure or mapping mismatch is `artifact_setup_failed` / `artifact_structure_mismatch`; it is **not** evidence that a source rule was preserved.

## Claim-independent observations

External artifacts have no Factgraph capability declaration to compare against. The live result therefore records the actual semantic observation directly:

- `preserved_or_stronger` — the invalid source witness did not survive the target implementation;
- `weakened` — the invalid source state was successfully realized;
- unresolved — the write was observed but a required post-state proof is unavailable/pending;
- not observed — the witness cannot be faithfully lowered under the current external-layout contract.

`preserved_or_stronger` is deliberately asymmetric. Rejecting one discriminating invalid population proves that witness is blocked; it does not prove exact semantic equivalence, and the target may enforce a stronger rule than the source.

## v1 external-layout contract

The first external PostgreSQL mapping is intentionally bounded:

- simple unquoted PostgreSQL identifiers;
- one physical table per source entity;
- one physical table per source fact;
- scalar field facts represented as columns;
- role references retain the same physical column arity as the canonical PostgreSQL decomposition;
- table and column names may be arbitrary when declared in the mapping;
- target constraints may differ freely from Factgraph's canonical output.

Not supported in v1:

- denormalized entity/fact co-location;
- one conceptual fact split across multiple physical tables;
- multiple conceptual objects sharing a physical table;
- views as writable mappings;
- triggers/functions requiring procedural DDL parsing guarantees;
- quoted/case-sensitive/schema-qualified physical identifiers;
- guessed or probabilistic mapping inference.

Those cases should gain an explicit richer mapping model later rather than be silently guessed.

## Falsification example

`examples/external_targets/postgres/` contains two externally styled implementations of the same source model:

- `value_range_preserved`: `Person.age` is renamed to `people_record.years` and a `0..130` CHECK is retained;
- `value_range_weakened`: the same physical layout deliberately omits the CHECK.

Both use the exact same source-semantic witness: a Person with `Age = -1`.

The hosted live CI expectation is:

| External artifact | Expected observation |
| --- | --- |
| preserved | `preserved_or_stronger` |
| weakened | `weakened` |

The packaging sandbox has no PostgreSQL service, so release artifacts keep this example generated/unobserved locally. Hosted CI is the place where the expectation becomes observed evidence.

## Why this milestone matters

Earlier Factgraph releases could prove or falsify **Factgraph's own mapping claims**. v0.14 begins testing the more product-relevant question:

> Given somebody else's physical implementation of a conceptual model, can the same semantic counterexample expose a real weakening?

That is the first step toward using Factgraph as a transformation test oracle rather than only as a compiler conformance suite.
