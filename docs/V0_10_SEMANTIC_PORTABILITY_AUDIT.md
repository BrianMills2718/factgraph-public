# v0.10 Milestone — Semantic Portability Audit

## Purpose

v0.10 pivots Factgraph from a broad semantic compiler/repository into an **auditor of semantic preservation across model/database transformations**.

The milestone does not delete the compiler, migration, metamodel, or repository work. It changes their role: they become infrastructure supporting an evidence-producing audit workflow.

## User-facing objective

The canonical workflow becomes:

```bash
factgraph audit MODEL \
  --targets postgres,mongo,typedb \
  --out-dir artifacts/audit
```

The important output is a semantic-preservation report, not merely generated target schemas.

## Required audit artifacts

At minimum:

```text
audit/
  audit.json
  audit.md
  obligations.json
  transformation_trace.json
  witnesses/
    index.json
    ...
  targets/
    postgres/
      verdicts.json
      live.json
    mongo/
      verdicts.json
      live.json
    typedb/
      verdicts.json
      live.json
  AUDIT_SUMMARY.json
```

Every artifact must be deterministic except fields explicitly identified as live-runtime observations/timestamps.

## Obligation model

An `Obligation` is the audit unit. It has:

- stable ID;
- source semantic element IDs;
- kind;
- human reading;
- formal/normalized parameters;
- target-independent witness requirements;
- source provenance if imported.

Initial obligation families should be derived from the existing normalized model:

- fact set semantics / uniqueness;
- preferred identification;
- mandatory participation where the current semantic model can state it;
- frequency bounds;
- numeric/value-domain constraints;
- subset/equality/exclusion;
- subtype inclusion;
- unordered-role canonicalization;
- logical symmetry;
- objectified fact identity/relationship attachment.

## Verdict model

A target verdict must separate representation from evidence:

```json
{
  "status": "represented_not_enforced",
  "mapping_evidence": [...],
  "structural_evidence": [...],
  "witness": "witnesses/...json",
  "live_execution": {
    "status": "not_run|accepted|rejected|error"
  },
  "preservation": "preserved|weakened|lost|unknown"
}
```

No static adapter result may be reported as live-observed preservation.

## Witness generation

v0.10 starts with deterministic, constraint-specific witness generators. Each generator should produce:

- a valid/control population when feasible;
- a violating population aimed at exactly one obligation;
- an explanation of what is intentionally violated;
- a list of surrounding obligations it is known to satisfy or cannot currently decide.

Later milestones may introduce property-based shrinking or SMT solving, but v0.10 must make the witness abstraction explicit now.

## External input

The Factgraph DSL remains supported as a reference/fixture language.

v0.10 must add at least one external semantic importer. Preferred first adapter: Apache Ossie ontology YAML/JSON, limited to an explicit supported subset and producing an import-loss report rather than guessing unsupported semantics.

Factum/FBM is the next priority. LinkML follows.

## Target strategy

### PostgreSQL

Retain current canonical adapter and conformance cases. Convert them into obligation-level audit evidence.

### MongoDB

Retain current validator/index adapter and explicit non-enforcement gaps. Convert them into obligation-level audit evidence.

### TypeDB

Add because it provides a meaningful semantic contrast:

- first-class n-ary relations;
- named/scoped roles;
- relation-as-role-player behavior;
- value constraints;
- lower/upper cardinality on `plays`, `relates`, and `owns`.

Do not add it merely as another output format; map only semantics that can be stated honestly.

### GraphQL

Keep existing SDL support but de-emphasize it in portability scoring because it is not a persistence enforcement system.

## Live CI

The existing local release environment has repeatedly lacked PostgreSQL/MongoDB/Docker/drivers. v0.10 must therefore include a CI workflow that can run the live evidence elsewhere.

A release report must distinguish:

- local static/structural evidence;
- CI live evidence;
- unavailable/not-run targets.

## Mutation testing

The target adapter tests must include mutations that deliberately weaken generated enforcement. A mutation is successful when the audit witness changes from rejected to accepted (or otherwise demonstrates the expected semantic regression).

Initial mutations:

- PostgreSQL: remove a `UNIQUE`;
- PostgreSQL: remove a value `CHECK`;
- MongoDB: remove a validator rule;
- MongoDB: remove a unique index;
- TypeDB: weaken a cardinality constraint.

## Starter benchmark corpus

Create `examples/portability/` with small models focused on one semantic obligation each. The initial target is 10–15 cases in v0.10 and at least 20 before v1.0.

Every case must contain expected audit results in a machine-readable file.

## Non-goals

v0.10 is not:

- a new visual editor;
- a complete ORM implementation;
- a new interchange standard;
- a Git replacement;
- a production migration orchestrator;
- proof of arbitrary model equivalence;
- a promise that finite witnesses prove all possible semantic properties.

## Implementation checkpoint — v0.10 release candidate

As implemented in the current tree:

- `factgraph audit` writes obligation-level reports, transformation traces, witnesses, per-target verdicts, and separate live evidence files;
- PostgreSQL and MongoDB reuse the existing conformance machinery under the obligation model;
- TypeDB is a third, semantically contrasting persistence target with deterministic TypeQL plus an optional live runner;
- both Apache Ossie and bounded LinkML inputs are supported, exceeding the original one-importer milestone;
- the public benchmark contains 20 focused models and 142 total obligations in the current static build;
- six deterministic target mutations are packaged;
- live PostgreSQL/MongoDB/TypeDB benchmark + mutation + external-input execution is configured in GitHub Actions;
- this packaging environment still does not contain a complete live service/driver stack, so local release evidence must remain `not_run` rather than being promoted to observed evidence.

One important self-falsification already occurred during implementation: the objectification benchmark exposed a missing PostgreSQL conformance case for a value constraint on an objectified relationship field. The generator was fixed before release.

## Completion criteria

- [x] `factgraph audit` exists and writes the documented file handoff.
- [x] PostgreSQL/Mongo capability/conformance data is reorganized into obligation-level verdicts.
- [x] Deterministic witnesses/recipes exist for a useful initial constraint subset.
- [x] External semantic import exists with explicit loss reporting (**two** formats: Ossie and LinkML).
- [x] TypeDB projection/capability/live-runner layer exists for a meaningful subset.
- [x] CI configuration exists for live PostgreSQL/MongoDB/TypeDB target execution.
- [~] Six target mutations and their discriminating live cases are packaged; unit/static mutation checks pass. A real live mutation run is still required before describing the mutation oracle as observed.
- [x] The portability benchmark contains **20** examples.
- [x] The retained compiler/migration/metamodel/repository test suite remains green.
- [x] The release audit is designed to say explicitly which evidence was live-observed and which remained `not_run`; the generated release report is authoritative for a particular package.


## v0.11 follow-on

v0.11 adds native Factum ORM JSON import plus an independent source-semantic population oracle and concrete isolated counterexamples. See `V0_11_EXTERNAL_AUDIT_AND_COUNTEREXAMPLES.md`.
