# v0.5 milestone — executable migration evidence

v0.5 turns the v0.4 migration planner into an optionally executable and verifiable harness without changing the v0.3 semantic language.

## Added

- `factgraph live-migrate`;
- explicit live migration execution policy;
- PostgreSQL transactional migration runner;
- PostgreSQL preflight evaluation;
- PostgreSQL live catalog/schema verifier;
- MongoDB structured migration-operation parameters;
- PyMongo migration runner;
- MongoDB live validator/index verifier;
- deterministic isolated namespaces;
- versioned migration fixtures;
- one passing and one intentionally failing risky-population fixture;
- Docker/live migration runner script;
- file-only live execution reports.

## Safety invariant

No safety class is silently promoted.

```text
safe                  -> executable
requires_data_check   -> blocked unless explicitly allowed + preflight passes
destructive           -> blocked unless explicitly allowed
manual                -> never generic-executable
```

For PostgreSQL, migration-stage failures roll the transaction back to the before schema.

For MongoDB, the harness explicitly records that generic DDL/document changes are not one transaction and relies on an isolated disposable database rather than claiming rollback.

## Verification invariant

A successful live command sequence is not enough.

When a target plan is fully executable under the selected policy:

```text
live_snapshot(execute(before_target, migration_plan))
    ==
project_target(after_model)
```

under the target-specific structural comparator.

When manual/blocked operations remain, the result must be `completed_partial` rather than a false full-success claim.

## Negative evidence invariant

A fixture that is valid under the before model but violates a newly gated after-model invariant should fail at **preflight**, not only when the DDL/index/validator command is attempted.

The packaged `safe_risky/fixture.fail.json` is designed to exercise this invariant against live services.

## Scope boundary

v0.5 is an executable verification harness, not a production deployment engine. Zero-downtime orchestration, domain-specific backfills, arbitrary type conversions, and distributed/resumable execution remain future work.
