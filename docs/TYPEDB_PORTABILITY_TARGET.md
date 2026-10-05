# TypeDB as a semantic portability target

TypeDB is included in v0.10 because it exercises different semantic boundaries than PostgreSQL or MongoDB.

## Current mapping

The deterministic TypeQL projection supports a bounded subset including:

- entity types;
- first-class n-ary relation types and named roles;
- relation-owned attributes for ValueType roles/relationship fields;
- relation types playing roles through objectification scenarios;
- single inheritance;
- single-attribute keys;
- ownership/role cardinality when Factgraph semantics align;
- scalar value ranges/enumerations.

## Deliberate gaps

Factgraph currently reports, rather than disguises:

- mathematical tuple-set identity for relation instances;
- compound preferred identifiers (`@subkey` is not treated as available in the current target contract);
- multi-role uniqueness that cannot be represented by one player cardinality;
- cross-fact subset/equality/exclusion;
- logical symmetry and unordered tuple identity where no equivalent target constraint is emitted;
- frequency lower bounds when mapping them to a target player cardinality would introduce total-participation semantics absent from Factgraph's frequency contract.

## Live execution

The optional `typedb-driver` runner:

1. creates an isolated database per runtime case;
2. applies the emitted schema in a schema transaction;
3. executes the generated witness in a write transaction;
4. observes acceptance/rejection at commit;
5. deletes the database.

A schema that merely parses is structural evidence, not live semantic evidence.

The packaged CI service uses TypeDB 3.12.1 and a 3.12-compatible Python driver range. Release environments without the driver/service report `not_run`.
