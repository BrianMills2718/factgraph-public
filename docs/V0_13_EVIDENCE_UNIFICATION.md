# v0.13 — evidence unification and portable live proof

## Purpose

v0.12 made one source-semantic counterexample the shared test oracle across PostgreSQL, MongoDB, and TypeDB. v0.13 fixes the next evidence-layer problem: the primary audit verdict must be driven by that shared semantic witness, not by the older backend-specific conformance fixtures.

The milestone also makes live observations portable. A CI run can be downloaded and re-applied to the same semantic model offline without reconnecting to the databases, but only after Factgraph proves that the evidence was generated for the exact same normalized model and exact same witness plan.

## Authoritative evidence rule

For each obligation/target pair:

1. a capability declaration says what the adapter claims;
2. structural checks test the generated target shape;
3. historical target-specific runtime cases remain useful secondary evidence;
4. the shared source-semantic witness is the authoritative live semantic channel;
5. only a fully asserted shared result may promote the primary verdict to `preserved_observed` or `weakened_observed`;
6. a shared result that contradicts the declared capability becomes `claim_falsified`;
7. a write that still requires an unresolved post-state query cannot promote the verdict.

This matters because the shared witness is derived from the source semantic oracle. The older target fixtures were designed independently and therefore cannot establish that the *same conceptual counterexample* was exercised across targets.

## Evidence fingerprints

Every v0.13 shared-witness plan/live report carries:

- `model_id`;
- `model_semantic_sha256` — SHA-256 of canonical normalized semantic JSON, excluding sample populations;
- `case_plan_sha256` — SHA-256 of the deterministic complete shared-witness case plan for that target.

Imported evidence is rejected unless both hashes exactly match the current audit model/plan. This prevents a result produced for an earlier schema revision, different identity mapping, or different witness-lowering implementation from silently changing today's verdict.

The fingerprint proves *content correspondence*, not organizational trust. It does not assert who ran the databases or whether the evidence file itself came from an authorized CI system. Repository/signing infrastructure can be layered around the file when provenance policy requires it.

## Portable evidence workflow

Run live elsewhere:

```bash
factgraph audit examples/portability/value_range.fg \
  --targets postgres,mongo,typedb \
  --postgres-dsn "$FACTGRAPH_POSTGRES_DSN" \
  --mongo-uri "$FACTGRAPH_MONGO_URI" \
  --typedb-address "$FACTGRAPH_TYPEDB_ADDRESS" \
  --out-dir live-audit
```

The authoritative evidence files are:

```text
live-audit/shared_witness_execution/postgres.json
live-audit/shared_witness_execution/mongo.json
live-audit/shared_witness_execution/typedb.json
```

Re-apply later/offline:

```bash
factgraph audit examples/portability/value_range.fg \
  --targets postgres,mongo,typedb \
  --shared-live-dir live-audit/shared_witness_execution \
  --out-dir replayed-audit
```

`evidence_import.json` records whether each target evidence file validated.

Supplying both a direct live connection and an imported evidence file for the same target is rejected as ambiguous instead of silently choosing one.

## CI replay gate

`scripts/verify_portable_live_evidence.py` validates the portability benchmark's saved shared-witness reports and rebuilds the asserted semantic verdicts without target connections. In hosted CI it runs with `--require-live` and fails if:

- a target evidence file is missing;
- its fingerprint does not match the current model/plan;
- a requested live runner did not complete;
- the live report itself failed; or
- the offline reconstructed asserted verdict differs from the original live audit.

This turns the CI artifact into a portable evidence package rather than an opaque log bundle.

## Nonclaims

v0.13 does not claim:

- that a SHA-256 fingerprint authenticates the actor who produced an evidence file;
- that a finite witness corpus proves logical equivalence for all possible populations;
- that rejecting an invalid population proves a target is neither stronger nor more restrictive in unrelated ways;
- that the packaging sandbox has observed live PostgreSQL/MongoDB/TypeDB results;
- that legacy target-specific conformance cases are useless; they remain valuable regression evidence but are secondary to the source-semantic witness for the product-level preservation verdict.

## Next product proof

With evidence authority corrected, the next high-value step is to run the same semantic oracle against **target artifacts produced by third-party transformation pipelines**, not only Factgraph-owned emitters. The auditor should be able to say whether an externally generated PostgreSQL schema, MongoDB validator/index set, or TypeDB schema preserves the imported source obligations and produce a minimal reproduction when it does not.
