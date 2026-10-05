# v0.11 Milestone — External ORM Audit and Source-Semantic Counterexamples

## Purpose

v0.11 makes the v0.10 semantic-portability pivot less self-referential in two ways:

1. Factgraph can audit a native **Factum ORM JSON** model directly, alongside Apache Ossie and LinkML.
2. Every obligation in the public portability corpus has a concrete target-independent **source semantic counterexample population**, not only a prose witness recipe.

The milestone keeps the Factgraph DSL as a fixture/debug language. It is not the required authoring surface for the audit product.

## Factum input contract

The bounded importer consumes Factum `.orm.json` v1/v2 documents using the public v2 schema as the current reference. It prefers `meta.guid` as cross-tool identity when present, otherwise preserves the Factum file-local ID.

Semantics imported when they have a direct representation in the current Factgraph kernel include:

- entity and value object types;
- reference-mode identification;
- n-ary fact types and role identities;
- canonical readings;
- objectification;
- single inheritance;
- uniqueness;
- one-role mandatory constraints;
- finite frequency constraints;
- logical symmetry where Factum's ring declaration matches the current kernel;
- two-sequence subset/equality/exclusion constraints;
- simple value enumerations and bounded inclusive ranges;
- fact sample populations without unknown (`null`) role values.

The importer explicitly reports rather than guesses unsupported or non-isomorphic constructs, including deontic constraints, disjunctive mandatory constraints, arbitrary role-based identification, non-symmetric ring families, role-scoped value constraints, subtype-set partitions, multiple inheritance, derivation rules, and richer value-range unions.

Target preservation verdicts apply only to the normalized imported semantic subset. Source import gaps remain a separate first-class section of every audit.

## Source semantic population oracle

`factgraph.population.SemanticPopulation` is a target-independent finite population representation:

- explicit object-type memberships;
- typed scalar/value instances;
- ordered fact rows in normalized role order.

`validate_population(model, population)` evaluates the current auditable semantic kernel independently of PostgreSQL, MongoDB, TypeDB, or any target emitter.

The oracle currently checks:

- role-player membership and fact arity;
- value domains;
- intrinsic fact-set semantics;
- uniqueness;
- total mandatory participation;
- finite frequency bounds;
- unordered role groups;
- logical symmetry;
- preferred identification over complete identifier tuples;
- subset/equality/exclusion projections;
- subtype population inclusion.

Fact duplicates are diagnosed separately from other constraints. Other constraints are evaluated over the **semantic set of fact tuples**, preventing a repeated physical row from spuriously increasing semantic frequency.

## Counterexample synthesis

For each audit obligation, `synthesize_counterexample(...)` builds a small population intended to violate exactly that obligation. The synthesizer uses deterministic bounded repair for surrounding constraints when necessary, for example:

- add required relationship facts for unrelated mandatory participation;
- add reverse tuples for unrelated symmetry;
- satisfy subset/equality context around a duplicate-fact or exclusion witness;
- populate unrelated preferred identifiers.

A counterexample status is one of:

- `isolated` — the semantic oracle reports the target obligation and no other obligation;
- `collateral` — the target is violated but interacting rules also reject the population;
- `not_independently_falsifiable` — the declared obligation is logically implied by stronger/current semantics in the bounded model (for example full-tuple uniqueness under intrinsic set semantics);
- `unsupported` — no bounded synthesizer exists for the obligation.

## Minimality claim

v0.11 deliberately does **not** claim a globally smallest counterexample.

Every isolated witness is passed through deterministic greedy deletion. The resulting `minimality` record can claim only:

> No single fact-row occurrence or typed population member can be deleted while preserving an isolated violation of the target obligation.

This is called **local irreducibility under single-element deletion**. A future solver-based implementation may establish stronger minima, but v0.11 does not imply that result.

## Public corpus result

The current 20-model portability corpus contains **142 semantic obligations**. The v0.11 source counterexample benchmark requires:

- all 142 target obligations to be observed by the source semantic oracle;
- zero collateral violations;
- every isolated witness to be locally irreducible under the documented deletion operation.

The authoritative generated result is:

`artifacts/semantic_counterexample_benchmark/summary.json`

## Important remaining gap

A source-level counterexample is not yet the same thing as a live cross-target proof.

The next evidence step is to lower the **same conceptual population** into target-native insert/write operations, execute it against PostgreSQL, MongoDB, and TypeDB, and attach the observed accept/reject result to the obligation. Existing backend-specific conformance cases remain useful evidence, but v0.11 does not falsely claim they are all projections of the exact source population.

## Reference sources used for the Factum boundary

Checked 2026-09-04:

- Factum repository: `https://github.com/Volland/factum-orm`
- Factum public v2 JSON schema: `https://raw.githubusercontent.com/Volland/factum-orm/main/schema/orm-model-2.schema.json`

The importer is intentionally bounded to constructs verified from that public schema/README rather than inferred from the Factum implementation.
