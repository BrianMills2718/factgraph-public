# Semantic diff and migration planning

factgraph v0.4 compares **normalized semantic models**, not emitted SQL/JSON text.

The migration workflow is:

```text
before.fg -> normalize ---\
                         semantic diff -> semantic migration plan
                         /              \
after.fg  -> normalize -/                -> PostgreSQL migration plan/scripts
                                           -> MongoDB migration plan/scripts
```

## Why semantic diff comes first

Textual schema diffs confuse changes that have very different meaning. For example:

- renaming a field;
- dropping a field and adding an unrelated one;
- making an optional field required;
- tightening a value range;
- adding uniqueness;
- changing the player type of a role.

v0.4 assigns every semantic change one of four safety levels:

- `safe` — no existing population needs inspection for the conceptual change;
- `requires_data_check` — the change is meaningful only after the existing population satisfies a precondition;
- `destructive` — applying the change may discard stored facts/values;
- `manual` — factgraph deliberately lacks enough information to invent a correct data-conversion/identity strategy.

Target adapters may be more conservative than the semantic layer. Whether a rename was aligned by stable identity or an explicit legacy hint, a semantic rename can be safe while a MongoDB physical rename is gated/manual because document fields and indexes must be coordinated.

## Stable identity and rename hints

v0.8 can carry an explicit semantic identity independently of a display name:

```text
entity User identity "user" {
  id userId: UserId identity "user.id"
}
```

If the name changes while the identity token remains the same, semantic diff aligns the element automatically and reports a rename rather than drop+add.

Legacy declarations without explicit identities retain deterministic name-derived IDs. For those models the diff engine still **never guesses renames**; explicit hints remain the compatibility mechanism.

Without a hint:

```text
Customer -> Client
```

is reported as `drop Customer` + `add Client`.

A hints file may establish identity explicitly:

```json
{
  "object_types": {
    "Customer": "Client"
  },
  "facts": {
    "OldMembership": "Membership"
  },
  "roles": {
    "Ownership.owner": "Ownership.client"
  },
  "fields": {
    "Customer.email": "Client.primaryEmail"
  }
}
```

Hints are validated against both models. Invalid or ambiguous mappings fail rather than being ignored.

## CLI

Semantic diff only:

```bash
factgraph diff before.fg after.fg \
  --hints hints.json \
  --out-dir artifacts/diff
```

Full migration planning:

```bash
factgraph migrate before.fg after.fg \
  --hints hints.json \
  --out-dir artifacts/migration
```

`--hints` is optional.

## Files produced by `migrate`

```text
before/
  normalized.fg
  semantic.json
  manifest.json
  validation.json
after/
  normalized.fg
  semantic.json
  manifest.json
  validation.json
hints.json
semantic_diff.json
semantic_diff.md
migration_plan.json
migration_plan.md
MIGRATION_SUMMARY.json
postgres/
  plan.json
  plan.md
  migration.sql
  migration.risky-preview.sql
  migration.destructive-preview.sql
  preflight.sql
mongo/
  plan.json
  plan.md
  migration.js
  migration.risky-preview.js
  migration.destructive-preview.js
  preflight.js
```

All migration artifacts are files. No migration command contacts a database.

## Script safety contract

### `migration.sql` / `migration.js`

Only operations marked safe/automatic are executable. Risky, destructive, and manual commands are rendered as blocked comments.

### `migration.risky-preview.*`

`requires_data_check` operations are rendered executable so an operator can inspect the intended sequence after running the supplied preflights. Manual and destructive operations remain blocked.

This is a **preview artifact**, not an approval to skip the data checks.

### `migration.destructive-preview.*`

Destructive operations are also rendered executable. The filename and plan both preserve their destructive classification. factgraph still does not execute them.

### `preflight.*`

Contains deterministic target-specific queries for checks the planner knows how to formulate, including:

- PostgreSQL null checks before `SET NOT NULL`;
- PostgreSQL duplicate checks before adding uniqueness;
- PostgreSQL validation queries before adding checks/foreign keys;
- MongoDB destination-field collision checks before `$rename`;
- MongoDB duplicate aggregation before creating unique indexes;
- MongoDB validator-match probes.

A successful preflight is necessary for a gated operation but does not turn unsupported semantics into target-native enforcement.

## PostgreSQL v0.4 behavior

Automatically planned where structurally safe:

- table renames from explicit semantic hints;
- column renames from explicit field/role hints when target column arity matches;
- creation of new tables;
- optional columns;
- relaxation of `NOT NULL`;
- creation of foreign keys on newly created empty tables.

Gated by data checks:

- `SET NOT NULL`;
- new uniqueness constraints;
- new `CHECK` constraints;
- foreign keys over existing rows.

Destructive and blocked by default:

- table drops;
- column drops.

Manual in v0.4:

- type conversions (a correct `USING` expression is domain-specific);
- primary-key/identity reshaping;
- removal of historically unnamed PostgreSQL `UNIQUE`, `CHECK`, or foreign-key constraints;
- subtype identity changes that cannot be reduced to safe structural renames.

The manual removal limitation is intentional: older canonical schemas used unnamed PostgreSQL constraints, so guessing server-generated names would be unsafe. A future canonical DDL version may name every constraint explicitly.

## MongoDB v0.4 behavior

Automatically planned where structurally safe:

- collection renames from explicit semantic hints;
- creation of new collections;
- safe validator relaxations/optional-property changes;
- removal of obsolete indexes when the exact canonical default index name is known.

A non-indexed field rename uses a staged preview:

1. temporarily relax the validator;
2. preflight that the destination field is absent;
3. `$rename` document data;
4. restore the final validator.

The whole sequence is gated; the safe-only script never weakens a live validator without also enabling the data step.

Indexed field/role renames remain **manual** because MongoDB index key paths do not rename with document fields, and naive unique-index staging can fail on missing-field uniqueness during a partial rewrite.

Adding a required field without a source-independent default is also manual: factgraph refuses to invent application data.

Dropping a conceptual field is destructive if stored values are to be removed. Updating `$jsonSchema` alone does not delete existing properties because the canonical schema does not set `additionalProperties: false`.

## No instance-data migration claim

v0.4 produces migration **plans and previews**. It can include simple physical data operations such as a MongoDB `$rename`, but it does not claim a general instance-data migration engine.

When a transformation needs domain knowledge—new required values, type conversion, identity reassignment, fact arity changes—the planner emits a manual step instead of manufacturing data.

## Examples

`examples/migrations/` contains:

- `safe_risky/` — additive changes plus new requiredness/value/uniqueness enforcement;
- `rename/` — explicit entity/field/role renames with a hints file;
- `destructive/` — field/fact/entity removal, blocked in safe scripts.

Build all migration examples with:

```bash
./scripts/build_migrations.sh
```
