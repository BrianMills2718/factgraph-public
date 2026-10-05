# External PostgreSQL artifact examples

Both examples are implementations of the same source model, `source_value_range.fg`, whose `Age` value type is constrained to `0..130`.

- `value_range_preserved/` renames the physical table/columns but retains a PostgreSQL `CHECK (years >= 0 AND years <= 130)`.
- `value_range_weakened/` uses the same renamed layout but deliberately omits the range check.

The mapping files are explicit semantic-to-physical manifests. They do not assert that either artifact preserves the source semantics. `factgraph audit-postgres-artifact` rewrites the same source-semantic counterexample (`Age = -1`) into each physical layout. A live run is required before either artifact gets an observed preservation result.
