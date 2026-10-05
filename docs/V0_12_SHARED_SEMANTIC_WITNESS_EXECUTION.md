# v0.12 — Shared Semantic Witness Execution

## Purpose

v0.12 closes a major methodological gap in the semantic-portability audit.

Earlier releases had two useful but separate kinds of evidence:

1. a target-independent source-semantic counterexample; and
2. backend-specific conformance cases.

Those are not automatically the same test. v0.12 introduces a stricter channel in which one conceptual counterexample is carried through the transformation pipeline and becomes the common oracle for PostgreSQL, MongoDB, and TypeDB.

The central question is now:

> Given a population that the source semantic oracle proves violates exactly obligation `O`, can the same conceptual population be represented in target `T`, and if so does the real target prevent or realize it?

This channel is additive. It does not replace the older target-specific conformance suite.

## Two witness levels

Factgraph deliberately keeps two different artifacts.

### 1. Locally irreducible semantic core

The v0.11 counterexample is the canonical source witness. It is target-neutral and greedily reduced until no single fact-row occurrence or typed membership can be removed while preserving the isolated target violation.

The claim is **local irreducibility under the documented deletion operation**. It is not a claim of global minimum size.

### 2. Target-independent executable contextual envelope

Some perfectly valid conceptual witnesses do not contain enough surrounding source facts to be physically inserted into a storage projection. For example:

- a standalone invalid value needs an owning field/fact occurrence before a database can store it;
- an objectified relationship field needs the relationship occurrence that owns it;
- a relationship that references an objectified fact needs an unambiguous relationship occurrence identity.

`prepare_execution_witness()` may therefore add the smallest currently known **source-semantic context**, once, before target lowering. The enriched population is shared by all targets.

Context is accepted only when the independent source oracle still reports exactly the original obligation and no additional violation. The canonical locally irreducible witness is never overwritten.

This distinction prevents target-specific fixture engineering from being confused with conceptual evidence.

## Population-level objectification occurrence identity

v0.12 adds an optional `SemanticPopulation` v2 envelope for execution fidelity.

It can bind an objectified instance to an exact occurrence of its objectified fact:

```text
objectified instance -> objectified type -> fact type -> row occurrence index
```

This is population identity, not a new conceptual modeling primitive. It is needed because an intentionally invalid population can contain two physically repeated relationship occurrences that are equal as tuples but must still be addressable independently while testing set semantics or fields on objectified occurrences.

Populations that do not need occurrence bindings continue to serialize as v1.

The validator checks binding type correctness, row existence, and injectivity. Older v1 fixtures remain valid and are not retroactively required to carry bindings.

## Lowering outcomes

Every target lowering has one of three statuses.

### `lowered`

Factgraph can emit a target program derived from the execution population with the stated fidelity.

### `representation_prevents_exact_realization`

The target projection itself prevents an exact representation of the invalid conceptual state.

Examples include:

- two conceptual values for a field compiled as one scalar SQL column or Mongo property;
- a subtype instance that is not a supertype instance when the target has native subtype entailment;
- a relationship that physically requires an identifier value which the source witness intentionally omits.

This status is evidence about the mapping/target shape. It is not silently replaced with a different handcrafted target witness.

### `unsupported`

Factgraph lacks enough lowering knowledge to determine the case faithfully.

The v0.12 contextual execution corpus currently has **zero** unsupported cases for the 20-model benchmark. That does not imply arbitrary models or future constraints are complete.

## Transport support identities

A target may need a physical address for something whose semantic witness intentionally omits a key.

When faithful observation is still possible, Factgraph may introduce a deterministic **transport-only support identity**. It is never inserted back into the source population and is never claimed to be domain semantics.

Current examples:

- PostgreSQL table-per-type subtype rows may need a deterministic key value so the target can attempt the subtype insert and let its FK enforce supertype inclusion;
- MongoDB may need the same kind of transport key to materialize the subtype document while deliberately omitting the corresponding supertype document.

The fidelity field records these cases separately from exact source-atom projection.

## PostgreSQL objectification lowering

For bound objectified relationship occurrences, PostgreSQL uses deterministic support IDs for generated identity columns and inserts them with `OVERRIDING SYSTEM VALUE`.

Objectified relationship-owned fields are projected onto the correct relationship row, and facts that reference an objectified relationship use the same physical identity.

If the source witness requires two simultaneous values for one scalar projected column, PostgreSQL reports `representation_prevents_exact_realization` rather than performing an update and pretending two conceptual values existed.

## MongoDB objectification lowering

MongoDB uses deterministic ObjectId-shaped support identities for bound objectified relationship occurrences. Relationship-owned fields are projected onto the correct document and references use the same `_id`.

As with PostgreSQL, a conceptual multivalue state for a scalar property is a representation-prevented case, not a successful exact lowering.

## TypeDB objectification lowering

TypeDB can keep the same relation variable for the objectified occurrence, use that relation as a role player in another relation, and attach owned attributes directly to the relation.

This means several objectification witnesses that require support identity in SQL/document targets remain close to direct source-atom projection in TypeDB. Multiple owned values can also remain explicit so cardinality enforcement can reject them.

## Write observation is not always enough

A successful invalid write is not necessarily proof that a target realized the semantic violation.

For obligations whose failure is defined by a missing counterpart or missing entailment, v0.12 requires a target-native **post-state query** after a successful write.

Current post-state families:

- mandatory total participation;
- subset;
- equality;
- symmetry;
- MongoDB subtype inclusion.

Examples:

- PostgreSQL executes a `SELECT COUNT(*) ...` query for the absent counterpart;
- MongoDB executes `count_documents` with a deterministic filter;
- TypeDB executes a read TypeQL query and counts returned concept rows.

If the write succeeds but the required post-state query has not run, the case remains **unproven**. It is not promoted to a semantic pass.

## Current 20-model benchmark boundary

The benchmark contains 142 source obligations.

### Strict locally irreducible source witness lowering

This deliberately attempts to lower the unchanged minimal source witness:

| Target | Lowered | Representation prevents exact realization | Unsupported |
|---|---:|---:|---:|
| PostgreSQL | 95 | 37 | 10 |
| MongoDB | 95 | 37 | 10 |
| TypeDB | 131 | 1 | 10 |

94 obligations lower exactly from the strict minimal witness to all three targets.

These numbers remain useful because they measure how often the minimal conceptual witness is already storage-ready without any contextual envelope.

### Source-contextual executable witness lowering

After target-independent, source-oracle-verified contextualization:

| Target | Executable/lowered | Representation prevents exact realization | Unsupported |
|---|---:|---:|---:|
| PostgreSQL | 104 | 38 | 0 |
| MongoDB | 104 | 38 | 0 |
| TypeDB | 141 | 1 | 0 |

That yields **349 executable target cases** across the 142 obligations and three targets.

There are currently **12 cases requiring post-state semantic proof, and all 12 have generated target-native post-state queries**.

## Live evidence status

The code paths exist for all three live targets and are wired into the portability benchmark/CI workflow.

The current packaging sandbox still has no PostgreSQL server, MongoDB server, TypeDB server, Docker/Podman, `psycopg`, `pymongo`, or installed TypeDB Python driver. Therefore the release must continue to report:

> **0 locally live-observed shared-witness obligations**

Generated readiness, executable programs, and post-state queries are not counted as observed target behavior.

A benchmark run that is supplied live target connections now fails if:

- a requested shared-witness runner does not complete;
- an executable case produces the wrong accept/reject result;
- a required post-state query contradicts the expected semantic result; or
- a required post-state check remains unresolved.

## Why representation-prevented cases matter

A portability auditor should not maximize an “executable percentage” by mutating the witness until the target accepts its shape.

If a conceptual invalid state is impossible to express because the target mapping collapses multiplicity, requires an absent key, or entails a supertype automatically, that is itself part of the semantic preservation story.

Factgraph therefore reports the boundary rather than replacing it with a different test.

## Evidence hierarchy after v0.12

1. **declared** — target adapter says how an obligation is represented;
2. **structurally checked** — generated target artifacts contain the claimed structure;
3. **source witness tested** — the target-neutral oracle has an isolated counterexample;
4. **shared witness lowerable** — that counterexample or its target-independent source-context envelope has a faithful target program;
5. **live write observed** — a real target accepted or rejected that program;
6. **post-state verified** — when acceptance is not sufficient, the resulting target state was queried and matched the semantic claim.

Only the last two are live backend evidence.

## Nonclaims

v0.12 does **not** claim:

- global-minimum counterexamples;
- that all 142 source obligations are physically executable on every target;
- that representation-prevented cases are backend bugs;
- that deterministic support identities are domain identifiers;
- that population-level occurrence bindings change conceptual objectification semantics;
- that a successful write proves an absence/entailment violation without post-state verification;
- live PostgreSQL/MongoDB/TypeDB evidence from the packaging sandbox;
- finite testing proves full logical equivalence of source and target models.

## Next proof obligation

The immediate next requirement is operational, not another modeling feature:

1. run PostgreSQL, MongoDB, and TypeDB in hosted CI;
2. observe the 349 currently executable shared-witness target cases;
3. run all 12 required post-state checks;
4. run the six mutation experiments against the live targets;
5. begin auditing external transformation pipelines rather than only Factgraph-owned mappings.

That is the shortest path from a technically coherent auditor to evidence that it catches real semantic regressions.
