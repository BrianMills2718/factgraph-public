# v1.0 Go / No-Go Gate

Factgraph should not reach v1.0 merely because the codebase is large or feature-complete. v1.0 means the semantic-portability thesis has evidence of usefulness outside Factgraph's own examples.

## Current checkpoint — v0.16

This is status, not a relaxation of the gate.

### Technical

- [x] Three external semantic formats: Apache Ossie, bounded LinkML, and native Factum ORM JSON.
- [~] PostgreSQL/MongoDB/TypeDB live CI workflow is configured, but configuration is **not** counted as an observed hosted run in the local release audit.
- [x] Per-obligation transformation trace files are generated.
- [x] The 20-case corpus has concrete source-semantic counterexamples for all 142 current obligations; every corpus witness is isolated and locally irreducible under single-element deletion. A shared target-independent execution envelope now lowers to 349 target cases across PostgreSQL/MongoDB/TypeDB, with representation-impossible cases kept explicit.
- [~] Six mutation experiments are packaged and unit-tested; live mutation detection must still be observed on real targets.
- [x] The benchmark has 20 nontrivial cases.
- [x] Reports separate declared, structural, witness, and live-observed evidence.
- [x] Value-domain audits include source-valid acceptance/boundary probes so blocking an invalid witness is not conflated with exact semantic equivalence.

### External usefulness

- [~] External conversion/mapping pipelines audited: **2 prepared / 0 live-observed / 3 required** (pinned LinkML 1.11.1 → PostgreSQL and Factum ORM 0.5.0 → PostgreSQL experiments are wired into hosted CI).
- [ ] Reproducible external semantic findings: **0 live-confirmed / 3 required**. The LinkML age-range and Factum mandatory-participation cases have falsifiable `weakened` hypotheses but neither is counted before live PostgreSQL observation.
- [ ] Independent user/maintainer confirmations: **0 / 2 required**.

The last three items cannot be satisfied by adding more Factgraph unit tests. They require work outside our own compiler.

## Required technical evidence

- [x] Import at least **two external semantic formats** without requiring authors to rewrite their model in the Factgraph DSL (**3 currently: Ossie, LinkML, Factum**).
- [ ] Run **PostgreSQL, MongoDB, and TypeDB live in CI**.
- [x] Produce a **per-obligation transformation trace** from conceptual rule to target constructs.
- [~] Generate a **minimal or deliberately small executable counterexample** for every supported auditable constraint family. Source-level isolated/local-irreducible witnesses are complete for all 142 corpus obligations. Target-independent contextual execution now yields PostgreSQL 104/142, MongoDB 104/142, and TypeDB 141/142 executable cases; the remaining target cases are classified as representation-impossible rather than silently replaced. Live observation remains outstanding.
- [x] Generate target-native post-state checks whenever successful writes alone cannot establish the semantic result (12/12 current required cases are query-ready).
- [ ] Use **mutation testing** to prove the auditor catches weakened target mappings.
- [ ] Maintain at least **20 nontrivial semantic-portability benchmark cases**.
- [x] Separate `declared`, `structurally_checked`, `witness_tested`, and `live_observed` evidence in every report.

## Required external evidence

- [ ] Audit at least **three external conversion/mapping pipelines or real model implementations**.
- [ ] Produce at least **three findings** that are not merely "feature unsupported" but demonstrate a concrete semantic weakening, ambiguity, or transformation error with a reproducible witness.
- [ ] Obtain at least **two independent external confirmations** that a Factgraph finding caught a bug, prevented a mistake, or changed a mapping/modeling decision.

## Product test

A technically correct report is not enough. At least two external users should be able to answer all of these without reading Factgraph internals:

1. What source rule is being audited?
2. How was it represented in the target?
3. What does the target really enforce?
4. What population demonstrates the gap?
5. Did the real target accept or reject it?
6. What should the reviewer do next?

## No-go outcome

If these requirements cannot be met after a bounded validation effort, stop broad product expansion. Publish Factgraph as:

- a research prototype;
- a semantic-portability benchmark/corpus;
- a reference implementation of evidence-oriented transformation checking.

That is a successful outcome if the broader product thesis is not validated.

## Feature admission rule

Until this gate is satisfied, a feature belongs on the active roadmap only if it directly improves one of:

- semantic preservation accuracy;
- witness generation;
- live target evidence;
- external-format interoperability;
- transformation traceability;
- report usefulness;
- benchmark credibility.
