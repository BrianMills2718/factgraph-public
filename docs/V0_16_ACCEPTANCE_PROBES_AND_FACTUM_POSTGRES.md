# v0.16 — acceptance probes + pinned Factum ORM → PostgreSQL audit

## Purpose

v0.16 tightens the semantic-preservation claim and prepares the second independently developed conversion pipeline.

A source-invalid counterexample answers only one question: **can the target realize a state the source forbids?** If the target rejects that witness, it may preserve the source rule—or it may be stricter than the source. Therefore a negative witness by itself cannot establish equivalence.

For value-domain obligations v0.16 also synthesizes selected **source-valid acceptance probes**. Numeric ranges use declared lower/upper boundaries; enumerations use declared allowed values. Every probe is checked by the source semantic population oracle before it can be emitted.

## External PostgreSQL result vocabulary

For a source-invalid witness plus any available source-valid acceptance probes:

- `weakened`: the invalid source population survives in the target;
- `preserved_on_tested_cases`: the invalid population is prevented and all generated source-valid boundary probes are accepted;
- `stronger_or_incompatible`: the invalid population is prevented but at least one generated source-valid probe is rejected;
- `preserved_or_stronger`: the invalid population is prevented but acceptance evidence is absent/incomplete;
- unresolved/not-observed states remain explicit when execution is unavailable.

`preserved_on_tested_cases` is deliberately **probe-scoped**. It is not a proof that the target is exactly equivalent to the source model.

## PostgreSQL external mapping v2

The bounded external-artifact audit still never reverse-engineers conceptual intent from arbitrary SQL. The user/converter supplies an explicit semantic→physical mapping bound to both the normalized model SHA-256 and external DDL SHA-256.

Mapping v2 additionally permits exact PostgreSQL catalog identifiers containing uppercase/mixed case. Mapping values store the exact identifier **without SQL quote characters**; the executor quotes them when required. This is necessary for independent generators such as Factum, whose PostgreSQL DDL deliberately uses quoted case-sensitive names.

The supported physical layout remains bounded to the entity/fact-table decomposition Factgraph can lower directly. Denormalized or arbitrary relational encodings remain unsupported until an explicit richer mapping model exists.

## Second independent pipeline: Factum ORM 0.5.0

The repository pins:

- project: `Volland/factum-orm`;
- package/version: `factum-orm@0.5.0`;
- reference source commit recorded in `examples/external_pipelines/factum_0_5_0/PROVENANCE.json`;
- generator command: `factum ddl <model.orm.json> --dialect postgres`.

The source model contains `Person`, `Skill`, and an m:n `PersonHasSkill` fact with a mandatory participation constraint on the Person role. The source-semantic counterexample is a valid `Person` occurrence with no corresponding `PersonHasSkill` occurrence.

The experiment hypothesis is that the untouched Factum relational projection permits that source-invalid population. This hypothesis is motivated by the mapper's bridge-table behavior, but **code inspection is not the finding**. Hosted CI must:

1. install exactly Factum ORM 0.5.0;
2. run Factum's own CLI to produce PostgreSQL DDL;
3. preserve/hash the generated SQL unchanged;
4. bind the expected physical names through the explicit mapping contract;
5. apply structural preflight against live PostgreSQL;
6. execute the source-semantic mandatory-participation witness;
7. record the database observation.

Only a live `weakened` observation counts as confirmation of the hypothesis. A different outcome falsifies the hypothesis and is equally valid evidence about the transformation.

## Local packaging limitation

The packaging sandbox does not have Factum ORM, PostgreSQL, Docker, or outbound package installation. Local release evidence therefore records the Factum generator as unavailable and `live_observed=false`. It must never be upgraded to a finding by reading generated/source code.

## v1.0 relevance

This milestone directly advances the external-evidence gate:

- independent pipelines prepared: LinkML 1.11.1 and Factum ORM 0.5.0;
- live-observed external pipelines: still zero in this packaging environment;
- confirmed external findings: still zero until hosted/live execution produces evidence;
- value-domain results are now able to detect selected target strengthening as well as weakening.
