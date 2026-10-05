# v0.17 — generalized source-valid acceptance cases

## Why this milestone exists

A negative counterexample answers one question: **can a source-invalid state survive the target?** If it does, the target is weakened for that obligation. If the target rejects it, however, rejection alone cannot distinguish source-equivalent behavior from a target that is simply stricter.

v0.16 added the dual direction for value domains. v0.17 generalizes that idea to selected relationship and identity obligations.

## Evidence directions

For an obligation `C`, Factgraph may now carry two independently source-validated populations:

1. **Counterexample** — satisfies the rest of the model and violates `C`.
2. **Acceptance probe** — satisfies the entire source model while exercising a permitted case of `C`.

The external target verdict remains deliberately finite and probe-scoped:

- `weakened`: the source-invalid witness survives;
- `preserved_on_tested_cases`: the invalid witness is prevented and every generated source-valid probe for that obligation is accepted;
- `stronger_or_incompatible`: the invalid witness is prevented but at least one generated source-valid probe is rejected;
- `preserved_or_stronger`: the invalid witness is prevented but positive evidence is absent/incomplete;
- unresolved/not observed: execution did not establish the required result.

`preserved_on_tested_cases` is **not** semantic equivalence. It means only that the finite generated negative and positive cases behaved consistently with the source rule.

## Positive probe families in v0.17

The bounded generator currently emits source-valid cases for:

- fact-set semantics: one ordinary fact occurrence;
- mandatory participation: an instance plus an actual participating fact;
- uniqueness: preferably two facts with distinct declared keys while other roles are held equal;
- preferred identification: two distinct entities with distinct source identifiers;
- frequency: realizable declared minimum/maximum counts for one observed key;
- value domains: range boundaries and allowed enumeration values;
- subtype inclusion: one subtype instance also present as its supertype;
- symmetric ring constraints: both directions of one relationship;
- unordered role groups: one representative occurrence;
- subset/equality: matching projected tuples on both sides;
- exclusion: disjoint projected tuples (or a one-sided valid case when the source domain cannot supply a distinct projected value).

Other ring properties and cases for which the bounded generator cannot produce a completely source-valid population remain absent. Absence is reported by omission; it is never replaced with a guessed positive witness.

## Full-source validation rule

Every acceptance probe is run through the same semantic population validator as counterexamples. A probe is emitted only when **zero source obligations are violated**. Bounded contextual repair may satisfy unrelated mandatory/subset/equality/symmetry obligations, but the final source oracle is authoritative.

## External PostgreSQL use

For a bounded external PostgreSQL mapping:

1. build the source-valid semantic population;
2. lower it using the same population-lowering layer used elsewhere;
3. rewrite only physical identifiers through the explicit SHA-bound mapping manifest;
4. recreate the untouched external DDL in an isolated PostgreSQL schema;
5. execute the probe;
6. combine positive and negative observations only for the same semantic obligation.

This keeps semantic intent in the source model and physical intent in the mapping file; Factgraph does not infer conceptual meaning from SQL names.

## Factum total-participation case

The pinned Factum ORM 0.5.0 experiment now carries both sides for the same mandatory-participation obligation:

- negative: a `Person` instance with no `PersonHasSkill` occurrence;
- positive: a `Person`, a `Skill`, and a `PersonHasSkill` occurrence connecting them.

If a live target accepts the negative case, the result is `weakened`. If it rejects the negative case but also rejects the valid participating case, the result is `stronger_or_incompatible`. Only if the invalid case is blocked and the generated valid case is admitted may the result be refined to `preserved_on_tested_cases` for this finite test set.

## Nonclaims

v0.17 does not claim:

- complete behavioral equivalence from finite examples;
- exhaustive positive-domain generation;
- global minimality of acceptance probes;
- a solver-complete model satisfiability procedure;
- live confirmation of the LinkML or Factum hypotheses in the packaging sandbox.
