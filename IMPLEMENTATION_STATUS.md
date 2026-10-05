# Strategic status update — 2026-09-04

## v0.18 provenance-locked upstream Factum control

Implemented in v0.18:

- external PostgreSQL mapping v3 with explicit bounded non-objectified binary-fact absorption into an anchor entity table;
- absorbed source operations coalesced into one entity row while repeated occurrences remain multiplicity-sensitive;
- absorbed mandatory post-state translation through non-anchor non-null physical columns;
- live physical-column preflight unions columns across intentional entity/fact table sharing;
- Factum reference-mode default typing aligned with Factum 0.5.0 Rmap conventions, preventing transport-type false positives;
- provenance manifest for exact Factum generator commit/blob/version and exact upstream `fig-mandatory.orm.json` commit/tree/blob/SHA-256;
- upstream control runner that refuses evidence on provenance mismatch and records unavailable checkouts honestly;
- hosted CI checkout of exact Factum ORM and book-model commits; Factum generator runs from the checked-in bundled CLI rather than npm registry installation;
- upstream functional mandatory model retained as a preservation control alongside the separate Factgraph-authored m:n weakening stress case.

Current package version: **0.18.0**

The v0.1–v0.9 implementation remains intact, but active product development has pivoted to **semantic portability auditing**. Repository/collaboration, metamodel, generic migration, and DSL breadth are now maintenance infrastructure unless they directly support preservation evidence. See `docs/STRATEGIC_REREVIEW_2026-09-04.md`, `docs/V0_10_SEMANTIC_PORTABILITY_AUDIT.md`, `docs/V0_11_EXTERNAL_AUDIT_AND_COUNTEREXAMPLES.md`, `docs/V0_12_SHARED_SEMANTIC_WITNESS_EXECUTION.md`, `docs/V0_13_EVIDENCE_UNIFICATION.md`, `docs/V0_14_EXTERNAL_POSTGRES_ARTIFACT_AUDIT.md`, and `docs/V1_GO_NO_GO.md`.

# Implementation status

## v0.17 generalized source-valid acceptance cases

Implemented in v0.17:

- source-valid acceptance probes now cover selected relationship, set, identity, subtype, and frequency semantics in addition to value-domain boundaries;
- each probe is validated against the complete normalized source model before it can become evidence;
- external PostgreSQL audits carry those positive probes through the exact same explicit physical mapping as the negative semantic witness;
- probe-scoped external verdicts are `preserved_on_tested_cases`, `stronger_or_incompatible`, `weakened`, or unresolved;
- the Factum ORM 0.5.0 total-participation experiment now has both a negative absence witness and a positive relationship-participation probe;
- finite source-valid probes remain a falsification aid, not an exact-equivalence proof;
- live LinkML/Factum PostgreSQL observations remain unconfirmed in this packaging sandbox because the external generators/database service are unavailable here.

Historical package version at that milestone: **0.17.0**

## v0.16 acceptance probes + second independent pipeline

Implemented in v0.16:

- source-valid acceptance probes for value ranges/enumerations, kept distinct from invalid source counterexamples;
- external PostgreSQL refinement states `preserved_on_tested_cases`, `stronger_or_incompatible`, `weakened`, and unresolved;
- no exact-equivalence claim from a finite probe set;
- external PostgreSQL mapping contract v2 with exact case-sensitive identifiers and SQL quoting;
- pinned Factum ORM 0.5.0 -> PostgreSQL hosted experiment using Factum's own `factum ddl` output unchanged;
- source mandatory-participation witness for the m:n Factum bridge experiment;
- Factum generator/version/provenance files and hosted-CI evidence handoff;
- local absence of Factum/PostgreSQL remains `generator_unavailable` / not observed, not a finding.

## v0.15 pinned third-party LinkML → PostgreSQL experiment

Implemented in v0.15:

- a public LinkML source case carrying `person.age` with `minimum_value: 0` / `maximum_value: 130`;
- a pinned external pipeline contract for `linkml==1.11.1`;
- hosted CI invokes LinkML's own `gen-sqltables --dialect postgresql` rather than Factgraph's PostgreSQL emitter;
- the untouched generated DDL is SHA-bound and passed into the v0.14 external PostgreSQL artifact auditor;
- the same source-semantic `age=-1` witness is used as the semantic test oracle;
- a dedicated PostgreSQL CI service requires an actual live observation and preserves the generator stdout/stderr, generated DDL, mapping, provenance, and audit result as an artifact;
- local/sandbox execution reports `generator_unavailable` when the pinned third-party package is absent and does not convert the hypothesis into evidence.

The expected live outcome is `weakened`, based on the pinned LinkML SQLTableGenerator implementation not constructing min/max CHECK constraints. This remains a **hypothesis until the hosted/live job executes the generated artifact**.

Historical package version at that milestone: **0.15.0**

## v0.14 external PostgreSQL artifact audit

Implemented in v0.14:

- audit externally produced PostgreSQL DDL without assuming Factgraph's canonical target capability declaration;
- explicit semantic-to-physical mapping manifest bound to the normalized source semantic hash and exact DDL hash;
- table/column/role/field rewrite of the same source-semantic witness into renamed external physical layouts;
- isolated live DDL + mapped-table/column preflight before semantic evidence is allowed;
- claim-independent observations (`preserved_or_stronger`, `weakened`, unresolved) for third-party artifacts;
- `postgres-map-template` and `audit-postgres-artifact` file-oriented CLI commands;
- preserving/weakened external value-range examples using the same `Age = -1` semantic witness;
- hosted-CI falsification step that requires the preserving artifact to reject that witness and the weakened artifact to realize it.

The external-layout contract is intentionally bounded to one table per source entity/fact with explicit simple identifier mappings. Arbitrary relational reverse engineering and intent inference remain out of scope. Local packaging evidence is still generated/unobserved because this sandbox has no PostgreSQL service.

Historical package version at that milestone: **0.14.0**

## v0.13 evidence unification and portable live proof

Implemented in v0.13:

- shared source-semantic witness execution now controls the primary live preservation verdict when fully asserted;
- legacy backend-specific runtime conformance remains secondary evidence and cannot outrank the shared semantic witness;
- generated/live shared reports carry semantic-model and exact case-plan SHA-256 fingerprints;
- imported live evidence is refused when either fingerprint differs;
- `factgraph audit --shared-live-dir` replays externally produced live evidence without database connections;
- target audit handoffs now include `semantic_live.json` separately from legacy `live.json`;
- hosted CI re-imports its own saved live evidence and proves that asserted semantic verdicts reconstruct identically offline.

The packaging sandbox still has no database services/drivers, so local release evidence remains generated/unobserved. The purpose of this milestone is to make hosted/external live evidence both authoritative and portable once it is produced.

Historical package version at that milestone: **0.13.0**

## v0.12 shared semantic witness execution

Implemented in v0.12 on top of the v0.11 independent source oracle:

- one shared source-semantic witness channel from obligation -> source counterexample -> source-contextual execution envelope -> target lowering -> live write/post-state result;
- target-independent execution contextualization that is accepted only when the source oracle still reports exactly the intended violation;
- optional `SemanticPopulation` v2 occurrence bindings linking objectified instances to exact relationship row occurrences while preserving v1 compatibility for populations that do not need bindings;
- objectification-aware PostgreSQL/MongoDB/TypeDB lowering, including relationship-owned fields and relationships that target objectified relationship occurrences;
- deterministic transport-only support identities where physical target addressing is required, without promoting those values into domain semantics;
- explicit lowering statuses separating executable cases, target-representation-impossible invalid states, and genuine unsupported lowerer gaps;
- target-native post-state verification for acceptance-sensitive mandatory/subset/equality/symmetry cases and MongoDB subtype inclusion;
- benchmark integration that fails requested live runs when write outcomes or required post-state results disagree with semantic expectations;
- strict-minimal lowering benchmark retained separately from the contextual execution benchmark.

Current 20-model / 142-obligation boundary:

- unchanged locally irreducible witness: PostgreSQL 95 lowered / 37 representation-prevented / 10 unsupported;
- unchanged locally irreducible witness: MongoDB 95 lowered / 37 representation-prevented / 10 unsupported;
- unchanged locally irreducible witness: TypeDB 131 lowered / 1 representation-prevented / 10 unsupported;
- target-independent contextual execution witness: PostgreSQL 104 lowered / 38 representation-prevented / 0 unsupported;
- target-independent contextual execution witness: MongoDB 104 lowered / 38 representation-prevented / 0 unsupported;
- target-independent contextual execution witness: TypeDB 141 lowered / 1 representation-prevented / 0 unsupported;
- 349 executable shared-witness target cases across all targets;
- 12 cases require acceptance-sensitive post-state verification and 12/12 have generated target-native queries;
- local observed shared-witness cases remain **0** because this packaging environment has no live PostgreSQL/MongoDB/TypeDB services or drivers.

The most important remaining evidence gap is now operational rather than representational: execute the shared-witness channel in hosted CI against all three real targets, run the mutation suite there, and then audit external mapping pipelines.

The v0.9 collaboration layer and all earlier compiler/migration/metamodel/repository functionality remain regression-tested supporting infrastructure, but new product work is frozen there unless it directly supports portability evidence.

Historical v0.12 package version: **0.12.0**

## Implemented now

- [x] `factgraph audit` semantic-portability workflow
- [x] obligation-level transformation traces and deterministic witness recipes
- [x] target verdicts for PostgreSQL, MongoDB, and TypeDB
- [x] separate per-target live evidence files; static claims are never promoted to observations
- [x] bounded Apache Ossie semantic importer with explicit gap reporting
- [x] bounded LinkML semantic importer with explicit gap reporting
- [x] bounded Factum ORM JSON importer with source identity preservation and explicit gap reporting
- [x] independent source-semantic population oracle
- [x] isolated source-level counterexamples for all 142 obligations in the 20-case public corpus
- [x] local-irreducibility evidence under deterministic single-element deletion
- [x] strict direct lowering benchmark for unchanged locally irreducible source witnesses
- [x] target-independent source-context execution envelopes validated by the semantic oracle
- [x] population-level objectification occurrence bindings for faithful physical lowering
- [x] shared source witness lowering into PostgreSQL/MongoDB/TypeDB target programs
- [x] explicit representation-prevents-exact-realization classification; no target-specific witness substitution
- [x] acceptance-sensitive post-state query generation for mandatory/subset/equality/symmetry and MongoDB subtype cases
- [x] shared-witness live runners integrated into the portability benchmark/CI path
- [x] 20-case public portability benchmark and machine-readable expectations
- [x] six deliberate adapter mutations and live mutation-oracle harness
- [x] TypeDB TypeQL projection/capability/conformance/live-runner layer
- [x] CI configuration for live PostgreSQL/MongoDB/TypeDB evidence
- [x] Python package with no mandatory runtime dependencies
- [x] lexer/parser for the v0.3 DSL
- [x] deterministic legacy name-derived semantic IDs
- [x] opt-in explicit rename-stable semantic identities (`identity "token"`)
- [x] per-build `identity_coverage.json` separating stable from legacy identity
- [x] entity/value/objectified fact types
- [x] n-ary facts and named roles
- [x] repeated player types
- [x] readings
- [x] entity field desugaring to facts
- [x] relationship field desugaring through objectification
- [x] simple and compound preferred identifiers
- [x] set semantics
- [x] uniqueness constraints
- [x] mandatory constraints
- [x] frequency constraints over role sequences
- [x] numeric value-range constraints
- [x] scalar enumeration (`oneof`) constraints
- [x] subset constraints over role-sequence projections
- [x] equality constraints over role-sequence projections
- [x] exclusion constraints over role-sequence projections
- [x] entity subtyping with single inheritance and inherited identity
- [x] subtype-cycle and incompatible-role validation
- [x] unordered role groups
- [x] distinct logical `symmetric` ring constraint
- [x] sample-population validation for uniqueness/value/frequency/set constraints
- [x] role-aware incidence index
- [x] model-shape analysis reports
- [x] canonical normalized printer
- [x] semantic-equality round-trip tests
- [x] deterministic PostgreSQL plan/emitter
- [x] PostgreSQL capability report
- [x] PostgreSQL table-per-type subtype projection
- [x] PostgreSQL native value checks and max-one frequency enforcement
- [x] PostgreSQL pure structural recovery report
- [x] PostgreSQL sidecar-assisted exact recovery
- [x] deterministic MongoDB plan/spec/script emitter
- [x] MongoDB capability report
- [x] MongoDB JSON-safe value validators and max-one frequency indexes
- [x] MongoDB subtype shape projection with explicit non-enforcement of population inclusion
- [x] MongoDB pure structural recovery report
- [x] MongoDB sidecar-assisted exact recovery
- [x] deterministic GraphQL SDL projection
- [x] GraphQL subtype/value/frequency/set-constraint metadata reporting
- [x] GraphQL capability report
- [x] CLI build that writes all outputs to files
- [x] automated test suite
- [x] deterministic PostgreSQL conformance populations
- [x] deterministic MongoDB conformance populations
- [x] conformance coverage for every native/emulated capability claim
- [x] structural conformance execution without external services
- [x] optional live PostgreSQL runner (`psycopg`)
- [x] optional live MongoDB runner (`pymongo`)
- [x] Docker Compose conformance environment
- [x] selected gap probes for `represented_not_enforced` semantics
- [x] explicit round-trip evidence report separating target structure from semantic sidecars
- [x] normalized semantic model diff
- [x] explicit rename hints; no heuristic rename guessing
- [x] safety classification for semantic changes
- [x] target-independent semantic migration plan
- [x] PostgreSQL schema migration planner and preflight generation
- [x] MongoDB schema/document migration planner and preflight generation
- [x] safe/risky/destructive migration preview tiers
- [x] migration examples for additive/tightening, rename, and destructive cases
- [x] optional live PostgreSQL migration executor with transactional rollback
- [x] live PostgreSQL preflight evaluation and schema verification
- [x] structured MongoDB migration-operation parameters
- [x] optional live MongoDB migration executor and target verification
- [x] versioned live migration fixtures with passing/failing risky populations
- [x] isolated disposable namespaces for live migration tests
- [x] `factgraph live-migrate` file-reporting CLI
- [x] canonical Factgraph metamodel represented as an ordinary Factgraph model
- [x] semantic-core metamodel population encoding/decoding
- [x] compiler-envelope metamodel population encoding/decoding
- [x] self-population validation using the ordinary semantic validator
- [x] metamodel self-description semantic + manifest round trip
- [x] deterministic second-encoding fixed-point check
- [x] per-build metamodel reification artifacts
- [x] `factgraph metamodel` and `factgraph reify` CLI file handoffs
- [x] packaged metamodel v1/v2 registry with the v0.6 contract preserved as v1
- [x] explicit v2 codec/semantic/manifest integrity facts
- [x] semantic diff between packaged metamodel versions
- [x] standalone metamodel-population migration by source decode + destination re-encode
- [x] deterministic filesystem model repository with immutable revisions
- [x] repository metamodel snapshots and declared metamodel version per revision
- [x] repository SHA-256 artifact verification and population replay
- [x] repository list/log/checkout/semantic-diff CLI handoffs
- [x] immutable repository-head migration between metamodel versions
- [x] repository refs with named branches and immutable tags
- [x] multi-parent revision DAG and DAG/ref integrity verification
- [x] deterministic three-way semantic merge over normalized elements
- [x] explicit merge conflict records and file-carried conflict resolutions
- [x] revision provenance (`author`, `message`, operation metadata)
- [x] append-only Ed25519 revision attestations stored outside immutable revision directories
- [x] signature verification integrated into repository verification

## Deliberately limited in v0.10

- Frequency bounds greater than one are semantically validated but not enforced by the canonical PostgreSQL/MongoDB mappings.
- Subtyping is entity-only, single-inheritance, and identity is inherited from the supertype.
- PostgreSQL enforces subtype inclusion using table-per-type PK/FK linkage; MongoDB and GraphQL retain subtype meaning but do not enforce cross-collection/population inclusion.
- Subset/equality/exclusion are validated against sample populations and retained in semantic/capability artifacts, but are not yet database-enforced by the canonical target adapters.
- Value ranges currently apply to numeric value types; `oneof` applies to scalar value types. MongoDB natively emits only constraints whose BSON literals are representable safely in the canonical pure JSON spec; Decimal/Date/Timestamp literal constraints remain explicit non-enforcement gaps.
- Sample populations contain fact instances, not a separate universe of entity instances, so mandatory participation and subtype population completeness are not inferred from absent samples.

- Explicitly identified elements align across renames automatically. Legacy name-derived elements still require explicit migration hints; Factgraph never heuristically guesses rename identity.
- PostgreSQL removal of historically unnamed UNIQUE/CHECK/FK constraints remains manual.
- MongoDB indexed-field/role renames remain manual because validator/data/index staging is not safely reducible to one generic sequence.
- Required-field backfills and type conversions remain manual because the compiler has no domain-specific source value.
- The `migrate` planner remains file-only. `live-migrate` is a separate opt-in harness that runs only inside a deterministic isolated schema/database.
- MongoDB live migration is not treated as one generic transaction; failures do not receive a false rollback claim.
- Live structural verification covers canonical target shape, not application-level behavioral equivalence or zero-downtime readiness.

- v0.6 self-hosting is semantic/metamodel closure, not compiler bootstrapping: Python classes, parser, normalizer, validators, and emitters are still handwritten runtime code.
- The canonical metamodel describes the current Factgraph normalized contract plus an explicitly separate compiler envelope; it does not claim complete Object-Role Modeling metamodel coverage or universality across modeling languages.
- Metamodel populations currently use the existing sample-fact population representation rather than a separate persistent object-instance repository.


- v0.9 repository collaboration adds filesystem remotes only. HTTP/SSH/cloud transports, garbage collection, rebasing, cherry-pick, force push, and history rewriting are not implemented.
- Stable semantic identities are opt-in. Legacy declarations remain deterministic but name-derived and therefore do not survive renames without hints.
- Semantic merge aligns normalized elements by semantic ID; it intentionally does not perform fuzzy/name-similarity matching or invent conflict resolutions.
- Merge conflict resolution is explicit (`ours`, `theirs`, `base`, `delete`). v0.9 explains conflicts in ORM/fact-model terms but still does not synthesize a domain decision automatically.
- Repository logical identity remains explicit through `model_key`; model-key renames are not guessed.
- Metamodel migration is only supported for packaged codec versions that can decode the old population and reproduce the declared semantic/manifest equality contract.
- Portable v2 Ed25519 attestations prove that a key signed immutable revision content/ancestry. v0.9 adds repository-local trusted/revoked key policy and protected-branch rules, but does not define organizational PKI, transparency logs, external key discovery, or distributed identity.
- Private signing keys are never repository artifacts by design; the deterministic demo key is generated only for release evidence and deleted immediately after signing.

## Intentionally not implemented yet

- [ ] arbitrary PostgreSQL reverse engineering
- [ ] arbitrary MongoDB reverse engineering
- [ ] GraphQL reverse reader
- [ ] multi-level/multiple inheritance beyond the current single-supertype entity model
- [ ] target enforcement of frequency bounds greater than one
- [ ] target enforcement of subset/equality/exclusion
- [ ] other ORM ring constraints
- [ ] general instance-data migration / backfill engine
- [ ] production/zero-downtime migration orchestration
- [ ] general rollback/resume engine for non-transactional MongoDB migrations
- [ ] query compilation
- [ ] graphical editor
- [ ] universal metamodel claims
- [ ] repository garbage collection / history rewriting / rebase/cherry-pick
- [ ] arbitrary metamodel-version migration without an explicit compatible codec

Retained v0.9 collaboration boundaries:

- [ ] HTTP/SSH/cloud remote transport adapters
- [ ] force push / history rewriting / rebase / cherry-pick
- [ ] distributed locking, consensus, or background synchronization
- [ ] organizational signer authorization beyond repository-local key rules
- [ ] transparency log / external PKI / key discovery
- [ ] automatic domain-aware conflict resolution (explanations are implemented; decisions remain explicit)

These omissions are scope boundaries, not silently missing features.

## Packaged-build execution note

The release environment does not contain PostgreSQL, MongoDB, TypeDB, Docker/Podman, `psycopg`, `pymongo`, or `typedb-driver`, and it has no outbound package-network access. Live conformance, live mutation, and live migration harnesses are implemented, but none is counted as observed against a real service while producing this ZIP. See `artifacts/ENVIRONMENT_CAPABILITIES.json`, `docs/SEMANTIC_PORTABILITY_AUDIT.md`, `docs/EXECUTABLE_CONFORMANCE.md`, and `docs/LIVE_MIGRATIONS.md`.
