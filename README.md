> **Public snapshot.** This repository is a public copy of a private development repository (`factgraph` at `f649054`, 2026-10-05). Development history is kept private. The recorded evidence folder (`artifacts/`, 37 MB) and the hash manifests that cover it are left out; the commands in this README regenerate the evidence. All 291 tests pass in this copy.

# factgraph

> **Global navigation:** use the [Vision knowledge index](https://github.com/BrianMills2718/vision/blob/main/wiki/index.md) as the canonical cross-repo entry point. This README remains the local entry point for this repository’s implementation, design, and evidence.

> **Strategic pivot (2026-09-04):** active development is moving toward **semantic portability auditing**: prove which conceptual/domain rules survive a transformation by tracing the mapping, generating discriminating populations, and executing them against real targets. The compiler/repository features below remain supported infrastructure, but are no longer the product center. See `docs/STRATEGIC_REREVIEW_2026-09-04.md`, `docs/V0_10_SEMANTIC_PORTABILITY_AUDIT.md`, `docs/V0_11_EXTERNAL_AUDIT_AND_COUNTEREXAMPLES.md`, `docs/V0_12_SHARED_SEMANTIC_WITNESS_EXECUTION.md`, `docs/V0_13_EVIDENCE_UNIFICATION.md`, `docs/V0_14_EXTERNAL_POSTGRES_ARTIFACT_AUDIT.md`, `docs/V0_15_THIRD_PARTY_LINKML_POSTGRES.md`, `docs/V0_16_ACCEPTANCE_PROBES_AND_FACTUM_POSTGRES.md`, `docs/V0_17_GENERALIZED_ACCEPTANCE_CASES.md`, `docs/V0_18_UPSTREAM_FACTUM_ABSORBED_CONTROL.md`, and `docs/V1_GO_NO_GO.md`.

A semantic-portability auditor with a fact-oriented conceptual kernel. It traces domain obligations through heterogeneous target mappings, generates source-semantic counterexamples, and keeps claimed enforcement separate from observed backend behavior.

The project deliberately separates:

1. **conceptual semantics** — object types, fact types, roles, readings, identifiers, constraints, objectification, subtyping;
2. **normalized incidence structure** — roles are first-class incidences, so repeated player types cannot collapse;
3. **target projections** — PostgreSQL, MongoDB, TypeDB, and retained GraphQL SDL;
4. **target enforcement** — what each target actually enforces;
5. **recovery metadata** — sidecar metadata is never confused with semantics natively present in a target.

This repository is an implementation of the accompanying design brief in `docs/CONTEXT_AND_ARCHITECTURE_BRIEF.md`.

**Cross-repo role.** Factgraph is the active design successor to
[`hypergraph-schema-ir`](https://github.com/BrianMills2718/hypergraph-schema-ir)
and owns semantic-portability auditing: trace obligations through transformations,
generate discriminating populations, and distinguish claimed enforcement from
observed target behavior. It is an auditor, not the universal application
semantic authority or a mandatory runtime layer.

For the current authority matrix, lineage dispositions, empirical gates, and
cleanup policy, see the [current ontology/semantic cluster architecture](https://github.com/BrianMills2718/vision/blob/main/wiki/synthesis/ontology-semantic-cluster-current-architecture-2026-09-07.md).
The earlier dated baseline is historical.

## Status

`0.18.0` adds provenance-locked **upstream-authored** Factum evidence and an explicit bounded mapping for functional binary facts that a relational mapper absorbs into an entity table. The external PostgreSQL mapping format is now v3: table-per-fact remains the default, while `storage_mode: "absorbed"` can explicitly bind a non-objectified binary fact to one entity-role table. Source-valid operations are coalesced into that physical row; repeated fact occurrences remain multiplicity-sensitive; mandatory post-state checks can test relationship presence through non-anchor non-null columns. Factgraph still never infers arbitrary denormalization from DDL.

The Factum importer also now mirrors Factum 0.5.0's documented default reference-mode typing so `refMode: "nr"` produces an integer source identifier rather than a string transport witness. Hosted CI no longer relies on npm for the Factum generator: it checks out the exact Factum ORM commit and exact `factum-book-models` commit, verifies commit/blob/tree/source hashes, and invokes the checked-in bundled CLI directly with Node. Two Factum cases are kept deliberately separate: the Factgraph-authored m:n mandatory stress case (weakening hypothesis) and upstream Factum's own functional mandatory model (preservation-control hypothesis). Neither hypothesis is a local finding; this sandbox still has no live PostgreSQL.

Shared-witness execution evidence in v0.12:

- strict unchanged minimal-witness lowering: PostgreSQL 95/142, MongoDB 95/142, TypeDB 131/142;
- target-independent contextual execution lowering: PostgreSQL 104/142, MongoDB 104/142, TypeDB 141/142;
- non-executable cases are classified as representation-impossible rather than replaced by handcrafted witnesses;
- 349 target-executable shared-witness cases across the corpus;
- 12/12 acceptance-sensitive cases have generated post-state queries;
- **0 local live observations** in the packaging sandbox; generated readiness is not reported as a pass.

External PostgreSQL artifact audit (v0.14):

```bash
# Generate a SHA-bound skeleton, then explicitly fill in the external physical names.
factgraph postgres-map-template examples/external_targets/postgres/source_value_range.fg \
  --schema examples/external_targets/postgres/value_range_weakened/schema.sql \
  --out mapping.json

# The repository example already contains the completed mapping.
factgraph audit-postgres-artifact examples/external_targets/postgres/source_value_range.fg \
  --schema examples/external_targets/postgres/value_range_weakened/schema.sql \
  --mapping examples/external_targets/postgres/value_range_weakened/mapping.json \
  --out-dir artifacts/external-pg
```

The mapping is explicit and SHA-bound; Factgraph does not guess conceptual intent from an arbitrary schema. See `docs/V0_14_EXTERNAL_POSTGRES_ARTIFACT_AUDIT.md`.

Pinned third-party LinkML pipeline experiment (v0.15):

```bash
# Hosted CI installs linkml==1.11.1 and runs this against a real PostgreSQL service.
python scripts/run_linkml_postgres_pipeline_audit.py \
  --out-dir artifacts/external_pipelines/linkml_1_11_1 \
  --postgres-dsn "$FACTGRAPH_POSTGRES_DSN" \
  --require-generator --require-live
```

The PostgreSQL DDL in this experiment is emitted by LinkML's own `gen-sqltables --dialect postgresql`; Factgraph only imports the source semantics, binds the resulting physical names, generates the witness, and observes the database. See `docs/V0_15_THIRD_PARTY_LINKML_POSTGRES.md`.

Generalized acceptance probes + provenance-locked Factum pipelines (v0.18):

```bash
# Local packaging remains honest if Factum/PostgreSQL are unavailable.
python scripts/run_factum_postgres_pipeline_audit.py \
  --out-dir artifacts/external_pipelines/factum_0_5_0

# Hosted CI checks out the exact Factum 0.5.0 Git commit, invokes its bundled CLI directly, and requires live PostgreSQL.
python scripts/run_factum_postgres_pipeline_audit.py \
  --generator .third_party/factum-orm/bin/factum.js \
  --package-json .third_party/factum-orm/package.json \
  --out-dir artifacts/external_pipelines/factum_0_5_0/stress_case \
  --postgres-dsn "$FACTGRAPH_POSTGRES_DSN" \
  --require-generator --require-live

# Provenance-locked upstream-authored preservation control.
python scripts/run_factum_upstream_postgres_pipeline_audit.py \
  --factum-repo .third_party/factum-orm \
  --model-repo .third_party/factum-book-models \
  --out-dir artifacts/external_pipelines/factum_0_5_0/upstream_fig_mandatory \
  --postgres-dsn "$FACTGRAPH_POSTGRES_DSN" \
  --require-checkouts --require-live
```

The Factum case asks whether a source mandatory-participation rule on a many-to-many `PersonHasSkill` fact survives Factum's own relational projection. A source-invalid population consisting of a `Person` with no bridge row is the oracle; source/code inspection motivates the hypothesis but is never counted as the observed result. External audits additionally execute finite source-valid probes (including value boundaries and selected relationship/identity cases) to detect target strengthening on tested cases. See `docs/V0_16_ACCEPTANCE_PROBES_AND_FACTUM_POSTGRES.md` and `docs/V0_17_GENERALIZED_ACCEPTANCE_CASES.md`.

Implemented:

- entity types and value types;
- numeric value ranges and scalar enumerations;
- entity subtyping with inherited identification;
- n-ary fact types;
- named roles and repeated player types;
- readings;
- field syntax desugared to binary facts;
- relationship fields desugared through objectification;
- simple and compound identifiers;
- uniqueness and mandatory constraints;
- frequency constraints over role sequences;
- subset, equality, and exclusion constraints over role-sequence projections;
- `unordered(...)` role groups;
- logical `symmetric` ring constraints kept distinct from unordered-role semantics;
- sample facts and semantic validation for uniqueness, values, frequency, subset/equality/exclusion;
- model-shape analyses (`uniform(n)`, `connected`, deliberately unevaluated `directed`);
- deterministic PostgreSQL emission;
- deterministic MongoDB collection plan/script emission;
- deterministic GraphQL SDL emission;
- per-target capability/enforcement reports;
- structural recovery reports for canonical PostgreSQL and Mongo artifacts;
- explicit semantic sidecars for exact self-generated round trips;
- deterministic positive/negative conformance populations for PostgreSQL and MongoDB;
- structural conformance checks and coverage accounting for every claimed native/emulated capability;
- optional live PostgreSQL/MongoDB conformance runners;
- explicit gap probes for selected semantics reported as not enforced;
- round-trip reports that distinguish native target structure from sidecar-assisted recovery;
- semantic diff between normalized model versions;
- explicit migration rename hints for object types, facts, roles, and fields;
- semantic change safety classification (`safe`, `requires_data_check`, `destructive`, `manual`);
- target-independent migration plans;
- PostgreSQL migration plans with preflights and safe/risky/destructive script tiers;
- MongoDB migration plans with validator/index evolution and staged non-indexed field renames;
- migration examples and deterministic file-only migration handoffs;
- opt-in live PostgreSQL migration execution with transactional rollback and schema verification;
- opt-in live MongoDB migration execution with explicit non-transactional semantics;
- versioned passing/failing live migration fixtures and file-only execution reports;
- canonical Factgraph metamodel expressed as an ordinary Factgraph model;
- deterministic semantic-core and compiler-envelope metamodel population encoders;
- metamodel population decoder with semantic and manifest equality contracts;
- self-description evidence in which the metamodel encodes, validates, decodes, and re-encodes itself;
- `factgraph metamodel` and `factgraph reify` file-only CLI handoffs;
- packaged metamodel v1/v2 registry plus semantic metamodel diff;
- population migration between compatible metamodel versions by decode/re-encode;
- immutable content-verified filesystem model repository;
- explicit rename-stable semantic `identity` tokens with per-build identity coverage reports;
- repository branches, immutable tags, multi-parent merge commits, provenance, and DAG verification;
- deterministic three-way semantic merge with explicit conflict files/resolutions;
- append-only Ed25519 revision attestations with repository signature verification;
- portable v2 revision attestations suitable for immutable remote transfer, with v1 verification retained;
- explicit trusted/revoked key policy and protected-branch signature requirements;
- content-addressed filesystem repository remotes with SHA-256 transfer packs;
- fetch-only remote-tracking refs and fast-forward-only push;
- ORM-aware merge conflict explanations emitted as JSON plus Markdown;
- repository semantic revision diff, checkout, log/list, verification, and metamodel-head migration.

Not claimed:

- arbitrary PostgreSQL or MongoDB reverse engineering;
- lossless cross-store data migration;
- complete ORM constraint coverage;
- database-level enforcement of every conceptual constraint;
- query compilation;
- GraphQL persistence semantics;
- a universal metamodel;
- Python/compiler bootstrapping from the metamodel;
- automatic merge conflict resolution, network/cloud remote transports, force push/distributed consensus, organizational PKI/transparency logs, or a claim that legacy name-derived IDs survive renames.

## Quick start

Requires Python 3.11+ and no mandatory runtime dependencies for static/structural auditing.

Primary workflow:

```bash
python -m factgraph audit examples/portability/mandatory_participation.fg \
  --targets postgres,mongo,typedb \
  --out-dir artifacts/audit-mandatory
```

External semantic inputs can be audited directly:

```bash
python -m factgraph audit examples/external/ossie_people.yaml --out-dir artifacts/audit-ossie
python -m factgraph audit examples/external/linkml_people.yaml --out-dir artifacts/audit-linkml
python -m factgraph audit examples/external/factum_people.orm.json --out-dir artifacts/audit-factum
```

Portable live evidence can be replayed offline after fingerprint validation:

```bash
python -m factgraph audit examples/portability/value_range.fg \
  --targets postgres,mongo,typedb \
  --shared-live-dir downloaded-evidence \
  --out-dir artifacts/replayed-audit
```

The directory must contain `<target>.json` shared-witness live reports produced for the exact same normalized model and exact generated witness plan.

The original compiler workflow remains available:

```bash
python -m factgraph build examples/richer_constraints.fg --out-dir artifacts/richer_constraints
```

When running from a checkout without installation:

```bash
PYTHONPATH=src python -m factgraph build examples/richer_constraints.fg --out-dir artifacts/richer_constraints
```

The build creates **files only** for all produced artifacts:

```text
normalized.fg
semantic.json
manifest.json
validation.json
analyses.json
incidence.json
BUILD_SUMMARY.json
identity_coverage.json
postgres/
  model.sql
  plan.json
  capabilities.json
  structure_recovery.json
  semantic.json
mongo/
  model.js
  spec.json
  plan.json
  capabilities.json
  structure_recovery.json
  semantic.json
graphql/
  schema.graphql
  capabilities.json
  semantic.json
conformance/
  coverage.json
  static_results.json
  live_status.json
  postgres/cases.json
  mongo/cases.json
roundtrip.json
metamodel/
  core.population.json
  envelope.population.json
  populated.metamodel.manifest.json
  recovered.semantic.json
  recovered.manifest.json
  recovered.normalized.fg
  roundtrip.json
```

## DSL

```text
model RicherConstraints {
  value PersonId: String
  value TeamId: String
  value Age: Int {
    range(0, 1000)
  }
  value Status: String {
    oneof("a001", "m002", "z003")
  }

  entity Person {
    id personId: PersonId
    age: Age
    status: Status
  }

  entity Employee {
    level: Status?
  }

  entity Team {
    id teamId: TeamId
  }

  subtype Employee is Person

  fact Membership(member: Person, team: Team) {
    reading "{member} belongs to {team}"
    frequency(member, 0, 1)
  }
}
```

Cross-fact set constraints are also supported:

```text
subset Approved(person, team) Membership(person, team)
equality Current(person) Listed(person)
exclusion Approved(person, team) Banned(person, team)
```

See `docs/SEMANTICS.md`, `docs/METAMODEL_SELF_HOSTING.md`, `docs/MODEL_REPOSITORY.md`, `docs/REPOSITORY_EVOLUTION.md`, `docs/COLLABORATION.md`, `docs/MIGRATIONS.md`, `docs/LIVE_MIGRATIONS.md`, and the examples for the current contract.

## Semantic diff and migrations

Compare two versions without diffing target DDL text:

```bash
PYTHONPATH=src python -m factgraph diff \
  examples/migrations/safe_risky/before.fg \
  examples/migrations/safe_risky/after.fg \
  --out-dir artifacts/diff-demo
```

Generate semantic + PostgreSQL + MongoDB migration plans:

```bash
PYTHONPATH=src python -m factgraph migrate \
  examples/migrations/rename/before.fg \
  examples/migrations/rename/after.fg \
  --hints examples/migrations/rename/hints.json \
  --out-dir artifacts/migration-demo
```

The safe migration scripts never execute destructive/manual operations and keep data-check-gated operations blocked. Separate risky/destructive **preview files** make the intended commands inspectable without pretending they were approved or executed. See `docs/MIGRATIONS.md`.

## Semantic collaboration

v0.9 can synchronize immutable semantic revisions between two filesystem Factgraph repositories without conflating transfer, trust, and merge:

```bash
factgraph repo-remote ./work origin ../hub --out remote.json
factgraph repo-fetch ./work origin team-app --branch main --out-dir artifacts/fetch
factgraph repo-push ./work origin team-app --branch main --out-dir artifacts/push
```

Fetch imports verified content and updates `origin/main` tracking only; it never advances local `main`. Push is fast-forward-only. Transfer packs store logical immutable files as SHA-256-addressed blobs.

Trust is a destination-local policy decision:

```bash
factgraph repo-trust-key ./hub release.pub.pem --label "release" --out trusted.json
factgraph repo-protect-branch ./hub team-app main --min-signatures 1 --key-id ed25519:... --out protected.json
```

A public key transferred with a signed revision is usable for cryptographic verification but is **not automatically trusted**. Protected remote pushes must satisfy the remote repository's explicit policy. Semantic merge conflicts additionally write `merge.explanation.md` using fact-model/ORM terminology. See `docs/COLLABORATION.md`.

## Core principle

The compiler does not claim that PostgreSQL, MongoDB, or GraphQL are unable to represent n-ary structure. The conceptual model exists to retain a canonical statement of **why the structure is there**, while each target may encode and enforce that intent differently.

A semantic feature may therefore be valid in the source model yet deliberately reported as `represented_not_enforced` in a target. That is evidence, not a compiler failure, provided the loss/enforcement status is explicit.

## Executable conformance

The ordinary build generates deterministic conformance cases but does not contact services. See `docs/EXECUTABLE_CONFORMANCE.md`.

To run against disposable Docker services on a machine with Docker:

```bash
python -m pip install -e '.[conformance]'
./scripts/start_conformance_services.sh
./scripts/run_live_conformance.sh
./scripts/stop_conformance_services.sh
```

Live results are written under `artifacts/live_conformance/`. Service/driver unavailability is recorded as `unavailable`, never as a pass.

## Live migration verification

v0.5 adds a separate opt-in executor on top of migration planning:

```bash
factgraph live-migrate examples/migrations/safe_risky/before.fg \
  examples/migrations/safe_risky/after.fg \
  --fixture examples/migrations/safe_risky/fixture.pass.json \
  --out-dir artifacts/live_migration \
  --postgres-dsn postgresql://factgraph:factgraph@127.0.0.1:55432/factgraph \
  --mongo-uri mongodb://127.0.0.1:57017 \
  --allow-risky
```

The harness creates an isolated before schema/database, loads an optional fixture, applies only policy-approved operations, evaluates gated preflights, and introspects the live target against the after projection. PostgreSQL migration execution is transactional; MongoDB explicitly makes no generic rollback claim and uses a disposable database. Manual operations are never executed automatically. See `docs/LIVE_MIGRATIONS.md`.
## Metamodel as data / semantic self-hosting

v0.6 represents the normalized Factgraph metamodel using Factgraph itself:

```bash
PYTHONPATH=src python -m factgraph metamodel --out-dir artifacts/metamodel_self
```

Reify any model as a population of that metamodel and decode it back:

```bash
PYTHONPATH=src python -m factgraph reify examples/richer_constraints.fg \
  --out-dir artifacts/reified_richer_constraints
```

The core population must decode to a semantically equal model. The separate compiler-envelope population additionally carries samples, field projection hints, and source-line provenance and must reproduce the complete manifest. The metamodel also passes the same round trip when it is the subject of its own population. See `docs/METAMODEL_SELF_HOSTING.md`.

This is a deliberately narrow self-hosting claim: the Python compiler is not generated from the metamodel, and no universality claim follows from semantic closure.


## Versioned metamodels and model repository

v0.7 preserves the v0.6 metamodel as version 1 and adds version 2 with explicit codec/integrity facts:

```bash
PYTHONPATH=src python -m factgraph metamodel-versions --out artifacts/metamodel_versions.json
PYTHONPATH=src python -m factgraph metamodel-diff 1 2 --out-dir artifacts/metamodel_diff
```

A stored metamodel population migrates through the represented semantic model rather than through row heuristics:

```bash
PYTHONPATH=src python -m factgraph metamodel-migrate old.population.json \
  --to-version 2 --out-dir artifacts/population_migration
```

Initialize and use a model repository:

```bash
PYTHONPATH=src python -m factgraph repo-init ./models.repo \
  --name MyModels --metamodel-version 1 --out artifacts/repo-init.json

PYTHONPATH=src python -m factgraph repo-commit ./models.repo examples/warehouse.fg \
  --model-key warehouse --out artifacts/commit.json

PYTHONPATH=src python -m factgraph repo-verify ./models.repo \
  --out artifacts/repository-verification.json

PYTHONPATH=src python -m factgraph repo-migrate-metamodel ./models.repo \
  --to-version 2 --out artifacts/repository-migration.json
```

Repository revisions store source, canonical/semantic forms, validation evidence, versioned metamodel populations, parent links, provenance, and SHA-256 hashes. A metamodel migration creates a new immutable revision but is expected to produce an empty **domain semantic diff**. See `docs/V0_7_MILESTONE.md` and `docs/MODEL_REPOSITORY.md`.

### Stable identities, branches, merge, and attestations

v0.8 adds an explicit identity layer. Names remain human-facing labels; `identity` tokens are the optional rename-stable semantic identity:

```text
model TeamApp identity "team-app" {
  value UserId: UUID identity "user-id"
  entity User identity "user" {
    id userId: UserId identity "user.id"
  }

  fact Membership identity "membership"(
    member: User identity "membership.member",
    team: Team identity "membership.team"
  )
}
```

Legacy declarations without `identity` keep deterministic name-derived IDs. Every build writes `identity_coverage.json` so the distinction is visible. Stable identities let a rename align automatically in semantic diff/migration; legacy models still use explicit migration hints rather than rename guessing.

Repository refs and semantic merge are file-oriented:

```bash
PYTHONPATH=src python -m factgraph repo-branch ./models.repo warehouse feature/redesign \
  --from-ref main --out artifacts/branch.json

PYTHONPATH=src python -m factgraph repo-tag ./models.repo warehouse v1 \
  --ref main --out artifacts/tag.json

PYTHONPATH=src python -m factgraph repo-merge ./models.repo warehouse \
  --ours main --theirs feature/redesign --out-dir artifacts/merge
```

Independent semantic edits merge by stable element identity. Modify/modify and delete/modify cases produce deterministic conflict records; resolutions must explicitly select `ours`, `theirs`, `base`, or `delete`. A committed merge has multiple parents and repository verification checks the DAG and refs.

Optional Ed25519 attestations are append-only and live outside immutable revision directories:

```bash
PYTHONPATH=src python -m factgraph repo-keygen --out-dir artifacts/keys
PYTHONPATH=src python -m factgraph repo-sign ./models.repo warehouse \
  --ref main --private-key artifacts/keys/private.pem --out artifacts/signature.json
PYTHONPATH=src python -m factgraph repo-verify-signatures ./models.repo \
  --model-key warehouse --out artifacts/signature-verification.json
```

This proves signature integrity against registered public keys; it does **not** define organizational trust, key revocation, remote identity, or authorization policy. See `docs/V0_8_MILESTONE.md` and `docs/REPOSITORY_EVOLUTION.md`.
