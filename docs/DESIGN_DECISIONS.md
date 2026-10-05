# v0.1 design decisions

This file answers the implementation questions from the architecture brief explicitly.

1. **Fact populations are sets.** Duplicate complete role tuples are the same fact.
2. **Repeated occurrences require identity.** Model an occurrence/event or objectify a fact; bags are not implicit.
3. **Objectification is explicit in the normalized model.** An `ObjectifiedFactType` references the fact type whose instances may play roles.
4. **Entity field syntax is sugar.** It normalizes to a binary owner/value fact.
5. **Relationship field syntax is sugar through objectification.** A relationship with fields is objectified automatically unless an explicit objectification name is supplied.
6. **`unordered` and `symmetric` are different.** `unordered` quotients fact identity under a role permutation; `symmetric` is a logical ring constraint requiring a companion fact.
7. **Role identity is semantic; ordinal is presentation order.** IDs include fact and role names. Ordinal controls deterministic printing and tuple order but is not the sole identity.
8. **Stable IDs are deterministic qualified strings.** v0.1 uses namespaced IDs such as `fact:Stocking` and `role:Stocking:part`. Persistent rename-stable UUIDs are a future migration feature.
9. **v0.1 constraints:** uniqueness, mandatory participation, preferred identification, unordered role groups, and the symmetric ring constraint.
10. **Graph-shape properties live under `analysis`, not domain constraints.** v0.1 implements `uniform(n)`, `connected`, and a deliberately unevaluated `directed` analysis.
11. **Semantic equality compares normalized schema semantics.** Mapping hints, source line notes, and sample populations are excluded from schema semantic equality.
12. **Pure target mode contains no semantic sidecar data.** Human generator comments are allowed; source concepts are not smuggled into the executable target.
13. **Round-trip mode uses explicit sidecars.** `semantic.json` is compiler-carried recovery metadata.
14. **Pure reverse readers return structural recovery reports, not invented conceptual models.** Sidecar-assisted readers return the exact normalized model.
15. **Silent drops are forbidden.** Target limitations appear in capability reports. `unsupported`/`lossy_dropped` are reserved for explicit failure/loss states.
16. **The relational mapping is canonical, not globally optimal.** Fields are absorbed as columns; source facts become relationship tables; objectified facts get identity; FKs are emitted after table creation.
17. **Only semantics-preserving deterministic transformations are used before emission.** Target optimization is intentionally minimal.
18. **Source line provenance survives normalization separately from semantics.** `source_notes` are diagnostic metadata and do not affect semantic equality.

## v0.3 additions

19. **Frequency and mandatory participation remain separate.** `frequency(roles, min, max)` constrains multiplicity of projections that occur; it does not close the world or imply every player participates. `mandatory(role)` remains total participation.
20. **Cross-fact set constraints are semantic role-sequence relations.** Subset/equality/exclusion compare projections of fact populations, never backend table/column names.
21. **Subtyping is conceptual population inclusion.** v0.3 permits one direct entity supertype, rejects cycles, and inherits the supertype identifier. Backend inheritance syntax is not the semantic definition.
22. **Value domains belong to value types.** Range/enumeration semantics are authored once and projected at every target use rather than being invented independently by emitters.
23. **Higher frequency and cross-fact constraints may remain unenforced without being lost.** The capability report must retain the semantic declaration and name the enforcement gap.
24. **Target fixtures must be semantically valid before they test a target rule.** Conformance generators choose values inside declared domains for positive/setup rows so an expected rejection is attributable to the feature under test.

## v0.4 additions

25. **Semantic diff precedes target diff.** Migration planning compares normalized conceptual models first; PostgreSQL/MongoDB structural plans are projections of that semantic change set.
26. **Renames are explicit, never heuristic.** Name-derived semantic IDs mean a rename must be declared in a migration hints file. Unhinted drop/add pairs remain drop/add even when names look similar.
27. **Migration safety is first-class.** Every semantic/target change is classified as `safe`, `requires_data_check`, `destructive`, or `manual`.
28. **Preview is not execution.** `migrate` writes scripts/plans only. Risky/destructive preview files make commands inspectable but do not record or imply that a database was changed.
29. **Manual means missing domain knowledge.** Required-field backfills, arbitrary type conversions, fact arity rewrites, and identity reshaping are not guessed from schema structure.
30. **Target migration risk may exceed semantic risk.** A conceptual rename is safe, but MongoDB may require a gated/manual physical sequence when indexes and document-field paths must move together.
31. **Safe scripts may be intentionally intermediate.** PostgreSQL can add a new required column as nullable in the safe tier, with backfill and `SET NOT NULL` left gated. The plan states that semantic convergence is incomplete until the gated step succeeds.
32. **Historically unnamed constraints are not guessed.** v0.4 will add PostgreSQL constraints but treats removal of old unnamed UNIQUE/CHECK/FK constraints as manual until canonical DDL adopts deterministic names.

## v0.5 additions

33. **Planning and execution are separate commands.** `migrate` remains side-effect free; only `live-migrate` may contact a target.
34. **Execution policy never upgrades `manual`.** Safe operations may run automatically, `requires_data_check` needs `--allow-risky` plus a passing preflight, destructive operations need a separate opt-in, and manual operations are never generic-executable.
35. **Live migration runs are isolated.** PostgreSQL uses a temporary schema; MongoDB uses a disposable database. Production namespace mutation is outside the v0.5 harness contract.
36. **PostgreSQL migration stages are transactional.** A gated or execution failure rolls the migration transaction back to the verified before schema.
37. **MongoDB generic rollback is not claimed.** The harness records non-transactional semantics and discards the isolated database on failure unless explicitly kept for inspection.
38. **Success requires introspection.** A command sequence that returns without error is not sufficient; the resulting live structure is compared with the canonical after-model target projection.
39. **MongoDB live execution uses structured operation parameters.** Generated JavaScript remains an inspectable artifact, but PyMongo executes planner data rather than parsing/evaluating JavaScript text.
40. **Migration fixtures are target-facing test populations, not a new semantic instance format.** They exist to test gates and live structural transitions without pretending to solve general data migration.

## v0.6 additions

41. **The metamodel definition is data.** `src/factgraph/data/factgraph_metamodel.fg` is authoritative and is parsed/normalized by the ordinary front end; the Python metamodel codec does not maintain a second schema definition.
42. **Self-hosting means semantic closure, not compiler bootstrapping.** The metamodel may describe itself as a population, but Python continues to implement parsing, normalization, validation, target emission, and execution.
43. **Core semantics and compiler envelope remain separate.** The core metamodel population carries `semantic_dict()` content. Samples, field projection hints, and source-line notes are encoded only in the explicitly labeled envelope population.
44. **Metamodel facts use absence for optional properties.** A constraint with no target fact has no `MM_ConstraintTargetFact` row; null sentinels are not introduced into the semantic population.
45. **Meta-object identity is explicit.** Source semantic IDs are the object tokens in metamodel populations, preserving repeated roles and cross-element references without reconstructing identity from names.
46. **Self-description must reach a deterministic fixed point.** Encoding the decoded metamodel must produce the same canonical metamodel population as the first encoding.
47. **Metamodel closure does not imply universal interpretation.** Other modeling languages may contain semantics not representable by the current Factgraph kernel; syntax encodability is not semantic equivalence.
48. **The existing sample-fact representation is the v0.6 population carrier.** A persistent general object-instance repository is a future product/research layer rather than a prerequisite for testing metamodel closure.
