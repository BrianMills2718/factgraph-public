# v0.2 milestone — executable conformance

## Goal

Turn target capability statements into falsifiable claims.

## Delivered

- deterministic valid/invalid target populations;
- structural conformance cases runnable without external services;
- coverage policy requiring every `native_enforced` / `emulated_enforced` PostgreSQL and MongoDB capability to have evidence;
- live PostgreSQL runner (optional `psycopg`);
- live MongoDB runner (optional `pymongo`);
- gap probes for selected deliberately non-enforced semantics;
- Docker Compose services and scripts;
- stronger round-trip report separating native structure from sidecar recovery;
- a model exercising total-participation `mandatory(role)` as a known target gap.

## Environment limitation of the packaged build

The build environment used to produce the release ZIP had no PostgreSQL server/client, MongoDB server/client, Docker, `psycopg`, or `pymongo`. Therefore live database cases were **generated but not executed here**.

That limitation is written to the packaged environment and quality reports. It is not treated as a passing live result.
