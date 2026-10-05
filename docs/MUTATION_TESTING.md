# Mutation testing the semantic oracle

A conformance system can become circular if the same compiler both emits a mechanism and declares that mechanism correct. v0.10 therefore packages target mutations that deliberately weaken one enforcement construct while keeping the source model and witness unchanged.

## Current mutation corpus

Six deterministic experiments are generated from the public portability benchmark:

1. PostgreSQL — remove an n-ary fact tuple key;
2. MongoDB — remove the corresponding compound unique index;
3. PostgreSQL — remove a value-domain `CHECK`;
4. MongoDB — remove a value-domain validator rule;
5. TypeDB — remove a total-participation `@card(1..)` player constraint;
6. TypeDB — remove a single-field `@key`.

Each mutation names the exact live conformance case expected to flip.

## Oracle

A mutation experiment passes only when:

- the unmutated live baseline completed and passed;
- the mutated target artifact completed;
- at least one named affected witness fails its original expectation under the mutation.

Unavailable services are `not_observed`, not passing mutation evidence.

Run:

```bash
python scripts/run_live_mutation_audit.py \
  --postgres-dsn "$FACTGRAPH_POSTGRES_DSN" \
  --mongo-uri "$FACTGRAPH_MONGO_URI" \
  --typedb-address "$FACTGRAPH_TYPEDB_ADDRESS"
```

## First internal finding

While assembling the v0.10 benchmark, the objectification case revealed that Factgraph claimed native PostgreSQL enforcement for a Decimal value constraint projected onto an objectified relationship field but had no generated conformance case covering that claim. The generator was corrected before release.

That is precisely the intended purpose of the benchmark/mutation discipline: it must be able to falsify Factgraph's own evidence story.
