# Strategic Rereview — 2026-09-04

## Executive conclusion

Factgraph should stop trying to become another general-purpose modeling environment, schema language, migration platform, or model repository. The strongest path is to become a **semantic portability auditor for data-model transformations**.

The proposed product claim is:

> **Factgraph tells you which domain rules actually survive a database/model transformation — and proves it with executable counterexamples.**

This is a deliberate repositioning. The fact-oriented/hypergraph representation remains useful, but it becomes an implementation mechanism rather than the product headline.

## What the rereview changed

The v0.1–v0.9 work established substantial engineering infrastructure: a role-aware fact model, normalization, target adapters, capability reports, conformance-case generation, semantic diff/migrations, live-runner scaffolding, metamodel-as-data, and model repository/collaboration machinery.

That work is technically useful, but the 2026 landscape makes several earlier product directions poor places to compete:

- modern Object-Role Modeling tooling already covers n-ary facts, verbalization, rich constraints, conceptual-to-physical mappings, drift, interchange, examples, and agent interfaces;
- LinkML already provides a self-described metamodel, many downstream generators, and generator compliance tests;
- Apache Ossie is becoming an industry semantic interchange standard and now includes ontology concepts, roles, verbalizations, identification, constraints, and mappings;
- U-Schema/Orion research already addresses generic cross-model schema evolution;
- Atlas is mature in migration safety/linting;
- Data Contract CLI already connects contracts to live backends and tests data/schema obligations;
- Eclipse CDO/EMF already provides model repositories, branches, history, merge, and collaboration.

Therefore, **the bundle of Factgraph features is not itself a novelty claim**.

## What remains unusually promising

The most differentiated part of Factgraph is the combination of:

1. a conceptual rule expressed above any one storage technology;
2. an explicit transformation trace into a target representation;
3. a precise statement of what the target representation actually enforces;
4. a generated population that violates exactly the rule under test while satisfying the surrounding model as far as possible;
5. execution of that population against the real target system;
6. a preservation verdict backed by observed evidence.

The goal is not a feature matrix like "PostgreSQL supports foreign keys." The goal is a **model-specific proof obligation** like:

> The conceptual model requires every Person to play the employee role in at least one Employment. The PostgreSQL mapping has a non-null foreign key from Employment to Person, but that only proves every Employment has a Person. A database state containing `Person(42)` and no Employment rows is accepted, so the original total-participation rule is not preserved.

This is the product center for v0.10 and the proposed v1.0.

## Product definition

### Primary user question

> "If I translate this conceptual/data contract into this database or target model, which semantics survive, which weaken, which disappear, and can you prove it?"

### Primary output

The main artifact becomes a **Semantic Preservation Report**. For every constraint or semantic obligation it should contain:

- source semantic rule and reading;
- stable rule/element identity;
- target mapping trace;
- structural representation;
- enforcement classification;
- automatically generated valid population;
- automatically generated violating witness;
- live target execution result when available;
- preservation verdict;
- explanation of the semantic gap;
- optional remediation pattern.

### Preservation vocabulary

The existing target capability language remains useful but is made evidence-oriented:

- `preserved_native`
- `preserved_emulated`
- `represented_not_enforced`
- `metadata_only`
- `unsupported`
- `lossy_dropped`
- `not_tested_live`

These statuses must not be promoted from static reasoning to observed proof without a real target execution.

## What to freeze

The following areas become maintenance infrastructure rather than active product expansion:

| Area | Decision | Reason |
| --- | --- | --- |
| Factgraph DSL feature growth | Freeze except fixtures/debugging | Factum/NORMA/other ORM tooling should be preferred authoring surfaces |
| Visual editor | Do not build | Mature ORM/model tooling already owns this problem |
| Full ORM verbalizer | Do not prioritize | Existing ORM ecosystem competency |
| New interchange standard | Do not build | Prefer Factum/FBM, Apache Ossie, LinkML |
| More metamodel self-hosting | Freeze | Useful architecture, weak differentiator |
| Repository branches/remotes/signing | Freeze | Existing model/Git systems already cover this category |
| Generic migration platform | Freeze | Atlas/Orion and DB-native tools are stronger here |
| GraphQL persistence target | De-emphasize | It does not provide an interesting persistence-enforcement comparison |
| Arbitrary DB reverse engineering | Keep non-core | Intent recovery is underdetermined and distracts from preservation testing |
| More targets merely for count | Stop | Add a target only if it creates a meaningful semantic contrast |

## What to build

### 1. Real target execution in CI

PostgreSQL and MongoDB conformance cases must stop being packaged-only `not_run` evidence. They should execute in CI against disposable services on every relevant change.

Add TypeDB because its first-class n-ary relations/roles and lower-bound cardinalities provide a meaningful contrast with relational/document stores. Later consider RDF + SHACL because closed-world/open-world and validation behavior create another useful semantic boundary.

### 2. External model ingestion

The Factgraph DSL becomes a reference fixture format. v1.0 should work without requiring users to author it.

Priority:

1. Factum/FBM-family ORM models;
2. Apache Ossie ontology documents;
3. LinkML schemas.

The objective is interoperability, not another format competition.

### 3. Constraint-directed witness generation

For a rule `C`, seek a smallest finite population that:

- satisfies the rest of the modeled obligations as far as the supported finite semantics allow;
- violates `C`;
- can be projected into each target;
- can be executed against the target;
- shrinks to a minimal understandable counterexample.

Start with deterministic generators for supported constraint families. Property-based generation/shrinking is a reasonable implementation technique. SMT/bounded-model solving is a later option for interacting subset/equality/exclusion/ring constraints.

### 4. Transformation traces

Every target action must be attributable to conceptual source elements. The report should say not merely that a target has a `UNIQUE` or validator, but **which source semantic obligation it is intended to discharge**.

### 5. Mutation testing

A preservation auditor can accidentally agree with the compiler that generated the target. To avoid this circularity, deliberately mutate target mappings:

- remove a UNIQUE;
- remove/change a CHECK;
- weaken a validator;
- remove a foreign key;
- alter a TypeDB cardinality.

The generated witnesses must detect the semantic regression.

### 6. Public semantic-portability corpus

Build a small but rigorous benchmark around semantic difficulty, not application size. Each case should include:

- conceptual model;
- readings;
- valid population;
- minimal violating population;
- expected per-target preservation status;
- transformation trace;
- live observed outcome where the target is available.

Candidate constraint families include identification, n-ary uniqueness, total participation, objectification, subtype inclusion, frequency bounds, value constraints, subset/equality/exclusion, symmetry/unorderedness, and relationship-to-relationship references.

## Why this is complementary rather than competitive

### Factum/ORM tools

Use them for rich authoring, visualization, verbalization, validation, and interchange. Factgraph should audit whether a chosen downstream representation preserves the authored semantics.

### Apache Ossie

Treat Ossie as an input/interchange ecosystem, not an enemy. Its ontology work is moving toward entities/value types, relationships/roles, verbalizations, identification, constraints, derivations, and logical mappings. Factgraph can audit transformations from these semantic declarations into physical targets.

### LinkML

Use LinkML as an input and source of compliance-test ideas. LinkML's generator dashboard reports implementation support; Factgraph's stronger aspiration is **model-specific execution evidence** for the obligations in a particular model.

### Data Contract CLI

Data Contract CLI is strong at dataset contracts and live data/schema tests. Factgraph should sit one conceptual layer above it: test the semantics of a transformation, and where possible export concrete data-quality checks rather than reimplementing the entire data-contract ecosystem.

### Atlas

Atlas should remain a migration-safety peer/tool, not a target to outbuild. Factgraph's migration machinery is useful for experiments and semantic evolution, but database operational safety is not the new product center.

## Evidence standard

The project must distinguish four kinds of evidence:

1. **Declared** — an adapter claims a mapping/status.
2. **Structurally checked** — emitted target structure has the expected construct.
3. **Witness-tested** — a generated population is capable of discriminating preservation vs weakening.
4. **Live-observed** — the actual target accepted/rejected the population as predicted.

Only level 4 should be described as live preservation evidence.

## The v1.0 usefulness test

Before v1.0, require all of the following:

- import at least two external semantic formats;
- run PostgreSQL, MongoDB, and TypeDB live in CI;
- produce per-constraint transformation traces;
- generate minimal executable counterexamples;
- mutation-test target mappings;
- cover at least 20 nontrivial semantic patterns/constraint cases;
- detect meaningful issues in at least three external conversion/mapping cases;
- obtain at least two external confirmations that a report caught a bug or changed a modeling/mapping decision.

If these cannot be demonstrated, do not continue broad product expansion. The project can still be released as a research prototype and benchmark.

## Proposed v0.10 definition

**v0.10 = Semantic Portability Audit**

The milestone is successful when:

- `factgraph audit` is the primary workflow;
- the report is organized around semantic obligations, not targets/features;
- current PostgreSQL/MongoDB conformance generation is reused as evidence machinery;
- live CI is configured and reports observed vs unavailable evidence honestly;
- at least one external semantic-format importer exists;
- TypeDB exists as a semantically meaningful target candidate;
- a starter portability corpus exists;
- mutation tests show the auditor catches deliberately weakened mappings.

## Research sources

Research was rechecked on 2026-09-04. This list is intentionally a landscape map, not a claim that no other overlapping work exists.

1. Factum ORM package snapshot (Socket), rich ORM constraints/mappings/verbalization/interchange/CI/MCP: https://socket.dev/npm/package/factum-orm
2. Apache Ossie 2026 updates / Apache Incubator status: https://ossie.apache.org/updates/archive/2026/
3. Apache Ossie ontology specification: https://github.com/apache/ossie/blob/main/ontology/ontology.md
4. Apache Ossie ecosystem and converters: https://ossie.apache.org/ecosystem/
5. LinkML metamodel: https://linkml.io/linkml/schemas/metamodel.html
6. LinkML generator feature dashboard, generated from compliance tests: https://linkml.io/linkml/generators/dashboard.html
7. LinkML generators: https://linkml.io/linkml/generators/
8. TypeDB schema modeling / n-ary relations and cardinality: https://typedb.com/docs/guides/schema-modeling/
9. TypeDB `@card` reference: https://typedb.com/docs/typeql-reference/annotations/card/
10. Data Contract CLI PostgreSQL testing: https://docs.datacontract.com/testing/postgres
11. Data Contract CLI schema constraints: https://docs.datacontract.com/schema
12. Atlas migration analyzers: https://atlasgo.io/lint/analyzers
13. Eclipse CDO repository/branch/merge documentation: https://help.eclipse.org/latest/topic/org.eclipse.emf.cdo.doc/html/users/Doc05_UsingCheckouts.html
14. U-Schema/Orion schema-change taxonomy: https://arxiv.org/abs/2205.11660
15. Jadoon et al., *Model transformation and property preservation in rigorous software development: A systematic literature review*, JSS 2025: https://doi.org/10.1016/j.jss.2025.112508
16. TransforMMer / heterogeneous benchmark engineering, 2026: https://arxiv.org/abs/2607.07175

## Final strategic rule

A new Factgraph feature should now have to answer:

> **Does this make the semantic-preservation verdict more accurate, more executable, more interoperable, or more useful to a real reviewer?**

If not, it is probably not v1.0 work.
