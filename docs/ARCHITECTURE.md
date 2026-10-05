# Architecture

## Pipeline

```text
source DSL
  -> parser AST
  -> normalization/desugaring
  -> semantic Model
  -> incidence index
  -> validation + analyses
  -> target adapters
       -> PostgreSQL
       -> MongoDB
       -> GraphQL

before semantic Model + after semantic Model
  -> semantic diff + explicit rename alignment
  -> target-independent migration plan
  -> migration target adapters
       -> PostgreSQL plan/preflight/scripts
       -> MongoDB plan/preflight/scripts
```

The semantic `Model` is authoritative. Target-specific concepts do not live in the semantic package.

## Semantic kernel

The kernel contains:

- `EntityType`
- `ValueType`
- `ObjectifiedFactType`
- `FactType`
- `Role`
- `Reading`
- `Constraint` (including value, frequency, set, subtype, identification, role, and ring semantics)
- `SampleFact`

Fields are not semantic primitives. Normalization turns field syntax into binary facts and stores projection hints separately for deterministic schema mapping.

## Incidence IR

`IncidenceIndex` exposes the role-aware bipartite structure:

```text
ObjectType <--- Role ---> FactType
```

The role object is the incidence. This prevents repeated player types from collapsing.

## Target adapters

Adapters are deterministic pure transformations from the semantic model plus mapping hints.

Every build emits:

- target artifact;
- capability/enforcement report;
- pure-target structural recovery report when implemented;
- semantic sidecar for exact self-generated recovery.

The sidecar is intentionally separate from the pure artifact.

## Recovery

PostgreSQL and MongoDB have structural readers for the compiler's own canonical target surfaces. They report structure and unrecoverable intent rather than fabricating a conceptual model.

Exact semantic recovery is available by combining the target artifact with the emitted semantic sidecar.

GraphQL has no reverse reader in the current v0.5 implementation.

## Conformance layer

The semantic-to-target boundary is now tested by a separate conformance module. It generates deterministic positive/negative populations from the same normalized model and maps those cases back to capability source-element IDs. Live target runners are optional adapters under `factgraph.live` and are not dependencies of the semantic compiler.

## Migration layer

`factgraph.diff` compares normalized semantic models and retains explicit alignment supplied through `MigrationHints`. The diff engine does not inspect generated SQL or MongoDB text to infer conceptual changes.

`factgraph.migration` turns the semantic diff into a target-independent step list. `factgraph.migrations.postgres` and `factgraph.migrations.mongo` then compare canonical target plans and emit target-specific operations, preflights, and safety-gated previews.

The migration **planning** layer is file-only and side-effect free. The separate v0.5 `live-migrate` layer may execute a target plan only under an explicit policy. Planning deliberately distinguishes:

- semantic safety;
- target physical safety;
- data preconditions;
- destructive cleanup;
- manual transformations requiring application knowledge.

## Live migration execution layer (v0.5)

The v0.4 planner remains pure. v0.5 adds an optional executor after target planning:

```text
semantic before/after
  -> target migration plan
  -> execution policy
  -> isolated live target
       -> create before projection
       -> load optional migration fixture
       -> preflight gated operation
       -> execute allowed operation
       -> live introspection
       -> compare with after projection
```

`factgraph.live.postgres_migration` executes the migration stage inside a PostgreSQL transaction and verifies the resulting isolated schema against the canonical after `PgPlan`. A gated/execution failure rolls the migration stage back.

`factgraph.live.mongo_migration` executes structured migration operations through PyMongo. It does not evaluate the generated JavaScript preview and does not claim a generic MongoDB migration transaction. Failure state is contained by the disposable database.

The live layer is optional and imports `psycopg`/`pymongo` only when invoked. No target driver leaks into the semantic kernel or migration planner.

## Metamodel-as-data layer (v0.6)

The normalized model schema is now represented by a canonical Factgraph source file:

```text
src/factgraph/data/factgraph_metamodel.fg
```

It is parsed and normalized through the same front end as user models. There is no separate privileged metamodel parser.

The metamodel codec adds a higher-order path alongside target compilation:

```text
semantic Model
   -> metamodel population encoder
   -> populated FactgraphMetamodel
   -> ordinary population validation
   -> metamodel population decoder
   -> recovered semantic Model
```

Two populations are deliberately distinguished:

- **core** — the canonical conceptual/normalized semantics;
- **envelope** — core plus samples, field projection hints, and source provenance used by the compiler surface.

The core recovery invariant is semantic equality. Envelope recovery additionally requires full manifest equality.

The self-host check uses `FactgraphMetamodel` itself as the subject. The release requires the self-population to validate, decode identically, and produce the same population when encoded again.

This layer does not alter target projection or migration semantics. It is an additional representation of the authoritative semantic model and is kept independent of PostgreSQL, MongoDB, GraphQL, and live database drivers.
