# v0.4 milestone — semantic schema diff and migration planning

## Goal

Turn factgraph from a one-model compiler into a compiler that can reason about **change between two conceptual models** without collapsing semantic intent into a target DDL diff.

## Delivered

### Semantic comparison

- normalized model-to-model diff;
- explicit rename hints for object types, facts, roles, and fields;
- no heuristic rename guessing;
- change records for objects, facts, roles, objectification, fields, readings, value domains, constraints, and subtyping;
- deterministic `safe`, `requires_data_check`, `destructive`, and `manual` classification;
- JSON and Markdown diff artifacts.

### Target-independent migration plan

- one step per semantic change;
- preconditions retained from the semantic diff;
- explicit automatic/blocked status;
- JSON and Markdown handoff artifacts.

### PostgreSQL planner

- table/column renames;
- new-table/new-column creation;
- staged required-column additions;
- nullability migration;
- uniqueness/check/FK preflights;
- safe/risky/destructive script tiers;
- explicit manual treatment of type/identity changes and unnamed-constraint removals.

### MongoDB planner

- collection renames;
- validator evolution;
- staged non-indexed field renames;
- index comparison/create/drop;
- required-field backfill gaps;
- indexed field/role rename escalation to manual;
- destructive stored-field cleanup preview;
- safe/risky/destructive script tiers.

### File-only workflow

Every migration result is written under an output directory. The planner never contacts PostgreSQL or MongoDB.

## Non-goals

v0.4 does **not** claim:

- automatic arbitrary data conversion;
- automatic inference of renames;
- live migration execution;
- zero-downtime online migration orchestration;
- rollback correctness for arbitrary destructive/manual changes;
- migration between unrelated arbitrary database schemas;
- complete target enforcement of every ORM semantic constraint.

## Key invariant

A target migration plan is a projection of the semantic diff, not the source of truth for it.

If PostgreSQL and MongoDB require different physical operations for the same conceptual change, both target plans retain the same semantic before/after context rather than forcing one target's representation to define the model.
