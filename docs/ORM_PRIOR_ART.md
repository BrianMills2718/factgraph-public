# ORM / fact-modeling prior-art context

The project is intentionally ORM-inspired rather than presented as a new discovery that n-ary relationships exist.

Relevant concepts from Object-Role Modeling / fact-based modeling:

- object types (entity/value types);
- fact types as predicates over roles;
- first-class roles, especially when the same player type appears more than once;
- readings/verbalizations;
- uniqueness and mandatory-role constraints;
- reference/identification schemes;
- objectification/reification of a fact instance when the relationship itself deserves object status;
- sample populations as a conceptual validation aid.

Victor Morgante's Boston ORM metamodel writings are particularly relevant because they model roles, fact types, readings, constraints, and objectified fact types as explicit metamodel elements. His n-ary graph-database writing is also direct prior art for the observation that ternary facts require reification/bookkeeping in binary-edge graph representations.

This implementation does **not** depend on the broader claim that one ORM metamodel is a universal meta-metamodel. That remains a possible research direction after the compiler kernel is useful and testable.

## Positioning

The implementation's intended contribution is not “hypergraphs support n-ary edges.” It is the engineering synthesis:

- compact fact-oriented semantic kernel;
- role-aware incidence representation;
- deterministic heterogeneous schema projections;
- explicit capability/enforcement accounting;
- explicit separation of pure target semantics from recovery metadata;
- measurable self-generated round trips.

See `CONTEXT_AND_ARCHITECTURE_BRIEF.md` for the longer rationale and source list.
