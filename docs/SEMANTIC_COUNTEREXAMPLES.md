# Source-Semantic Counterexamples

Factgraph's central audit question is not just "can the target represent this construct?" but:

> What finite population demonstrates the semantic obligation, and what happens when that population is projected into the target?

v0.11 introduced the source oracle; v0.12 adds a shared execution layer without changing the canonical minimal witness.

## Independent oracle

`SemanticPopulation` is target-neutral. It does not know about tables, documents, indexes, or TypeQL. `validate_population` checks the normalized conceptual model directly.

That gives every target test a reference truth condition independent of the target adapter.

## Witness lifecycle

For an obligation `O`:

1. synthesize a source population `P`;
2. validate `P` against the conceptual model;
3. require `O` to be violated;
4. for an `isolated` result, require every other modeled obligation to remain satisfied;
5. greedily try deleting each fact-row occurrence and typed population member;
6. retain a deletion only if the population still violates exactly `O`;
7. record local irreducibility and population size;
8. later: lower `P` into each target and observe accept/reject behavior.

## Why duplicate rows are special

Facts are set-valued in the current kernel. A physically repeated row is therefore an error at the population boundary, but it does not become two conceptual facts for purposes of frequency, subset, equality, or symmetry calculations. This separation is necessary to make set-semantics witnesses logically meaningful.

## Evidence levels

Source counterexample evidence and target execution evidence are independent:

- source oracle says whether the population violates the conceptual rule;
- target adapter says how the rule/population is projected;
- live target execution says whether the real implementation accepts or rejects it.

A source witness can be fully isolated and locally irreducible while target live evidence remains `not_run`.

## Nonclaims

The source-counterexample and v0.12 shared-execution layers do not claim:

- global minimum counterexamples;
- completeness for arbitrary first-order constraints;
- proof that finite testing establishes full logical equivalence;
- solver-backed satisfiability;
- that backend-specific existing conformance cases are already exact lowerings of every source population.

## v0.12 shared execution witness

The canonical source witness remains the locally irreducible population described above. Physical targets sometimes require surrounding source-semantic context that the minimal witness deliberately omits. v0.12 therefore derives a second artifact, the **execution witness**, before any target-specific lowering.

The execution witness may add valid surrounding facts or memberships only when `validate_population` still reports exactly the original obligation. The same enriched source population is then offered to PostgreSQL, MongoDB, and TypeDB. It is not separately customized per target.

Objectification-sensitive execution populations may use the optional population v2 occurrence-binding envelope so an objectified instance can identify one exact relationship row occurrence. This is execution/population identity, not a change to conceptual objectification semantics.

A lowerer reports one of:

- `lowered`;
- `representation_prevents_exact_realization`;
- `unsupported`.

The second status is important evidence. A scalar SQL column, for example, cannot simultaneously realize two conceptual field-value facts. Factgraph does not replace that source population with an `UPDATE` sequence and call it equivalent.

For mandatory participation, subset, equality, symmetry, and MongoDB subtype cases, a successful write requires a generated post-state query before the target result can count as semantically observed.

See `V0_12_SHARED_SEMANTIC_WITNESS_EXECUTION.md` for the complete contract and current benchmark counts.
