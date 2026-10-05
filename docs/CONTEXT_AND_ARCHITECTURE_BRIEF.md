# Context and Architecture Brief
## A fact-based / Object-Role Modeling semantic compiler with a role-aware hypergraph IR

**Audience:** coding agent starting a new implementation from scratch  
**Purpose:** give enough conceptual, architectural, and implementation context to build a principled successor to the current hypergraph schema-translation prototype  
**Status:** design brief, not a frozen specification

---

# 1. Executive direction

Do **not** start by building “a universal hypergraph language” and then try to attach semantics to it later.

Do **not** copy the full Boston/Object-Role Modeling metamodel wholesale and do **not** make Victor Morgante’s strongest “universal metamodel / Infinity Fountain” claims an engineering requirement.

Instead, build this stack:

```text
        ergonomic source language
      (fact-oriented, ORM-inspired)
                  |
                  v
      semantic conceptual model
    ObjectType / FactType / Role /
    Reading / Constraint / Objectification
                  |
                  v
      normalized role-aware IR
   typed incidence / hypergraph-like form
                  |
          +-------+--------+----------------+
          |                |                |
          v                v                v
     PostgreSQL         MongoDB         GraphQL
      compiler           compiler         schema
          |                |
          v                v
     narrow reader     narrow reader
          \                /
           +-------> recovered model
```

The **semantic foundation** should be fact-based / ORM-inspired.

The **hypergraph idea** belongs underneath that foundation as an implementation and normalization technique: a fact type is naturally an n-ary relation, and each role is an incidence connecting that fact type to the object type that may play the role.

The main research/engineering question is not:

> Can everything be encoded as a hypergraph?

That is too cheap to be interesting.

The useful question is:

> Can we define a small semantic model once, preserve its intent explicitly, compile it deterministically into structurally different target schemas, report exactly what each target can and cannot enforce, and recover the same semantic model from output we generated ourselves?

That is a tractable and testable project.

---

# 2. Where the current prototype has arrived

The current article/prototype already discovered several important principles that should be retained.

It currently demonstrates:

- n-ary relationships as first-class declarations;
- repeated participant types, e.g. two `User` roles in one fact;
- participant role names;
- relationship-level fields;
- vertex/entity-level fields and nullability;
- deterministic generated output;
- PostgreSQL, MongoDB, and GraphQL target generation;
- explicit target-specific enforcement differences;
- a four-state distinction that avoids treating “not checked” as “passed”;
- narrow PostgreSQL and MongoDB readers that read only the compiler's own generated subset;
- explicit accounting of information that does and does not survive a round trip;
- refusal to claim arbitrary PostgreSQL/MongoDB import or a universal exchange format.

Those are good instincts.

The current article’s strongest formulation is essentially:

> PostgreSQL keeps the generated structure comfortably. What may disappear is the explicit record of *why* that structure exists in the conceptual model.

That should remain a central design principle.

The successor project should preserve that discipline while replacing the ad-hoc semantic layer with a clearer fact-based metamodel.

---

# 3. Relevant prior art: Object-Role Modeling / Fact-Based Modeling

## 3.1 What ORM contributes

Object-Role Modeling (ORM) is a fact-oriented conceptual modeling method.

The important concepts for this project are not the graphical symbols. They are the semantics underneath them.

A useful minimal ORM vocabulary is:

- **Object Type**
  - **Entity Type**: identity-bearing objects such as `Person`, `Company`, `Warehouse`.
  - **Value Type**: values such as `PersonId`, `Email`, `Money`, `Date`.
- **Fact Type**
  - A predicate/relation over one or more roles.
  - Example: `Person works for Company`.
  - Example: `Part is in Bin in Warehouse`.
- **Role**
  - One argument position in a fact type.
  - A role is played by an object type.
  - Roles are first-class. This matters when the same object type occurs more than once.
- **Fact Type Reading**
  - Human-readable predicate wording such as `Part is in Bin in Warehouse`.
  - A fact type may have multiple readings, including inverse readings.
- **Constraint**
  - Uniqueness.
  - Mandatory participation.
  - Frequency/cardinality.
  - Value constraints.
  - Subset/equality/exclusion.
  - Subtyping.
  - Ring constraints on self-relations.
- **Reference / Identification Scheme**
  - How an entity instance is identified.
  - This maps naturally to keys in relational targets but should remain conceptual in the source model.
- **Objectification**
  - Treating an instance of a fact/relationship as an object in its own right.
  - This is the principled answer to “when is a relationship just a relationship, and when is it something with identity/attributes/other relationships?”

ORM is useful here because it has already spent decades giving names and semantics to problems that the hypergraph prototype has been rediscovering.

## 3.2 Why roles are the crucial bridge

The current prototype started from syntax like:

```text
connect User User Group
```

That is not enough as a semantic core.

There are two separate `User` positions. They may have different meanings:

```text
actor: User
recipient: User
group: Group
```

A plain mathematical hyperedge represented as a set is not a sufficient model of this distinction.

The correct bridge between ORM and hypergraphs is:

```text
FactType
    |
    +-- Role actor ------> User
    |
    +-- Role recipient --> User
    |
    +-- Role group ------> Group
```

The roles/incidences carry:

- identity;
- name;
- position/reading order;
- player type;
- constraint participation;
- possibly permutation/equivalence semantics.

This is better described internally as a **typed incidence structure** or **role-aware hypergraph** than as a simple hypergraph.

## 3.3 Morgante's Boston metamodel: what is relevant

Victor Morgante’s Boston metamodel work emphasizes:

- concepts/symbols within models;
- entity types and value types;
- fact types as atomic model elements;
- roles as explicit metamodel elements;
- fact type readings and predicate parts;
- sample fact populations;
- role constraints;
- objectified fact types;
- preferred identification/reference schemes.

This is highly relevant prior art.

His `Part – Bin – Warehouse` / `StockedItem` examples are extremely close to the n-ary relation examples in the hypergraph compiler. The conceptual overlap is not accidental.

His metamodel also makes an important point that should influence the implementation:

> A role should not be inferred from a column name or object-type occurrence. It should exist as its own semantic object.

That is a strong design principle.

## 3.4 Morgante's n-ary / hypergraph work

Morgante has also explicitly written about n-ary relationships in graph databases and the awkwardness of representing a ternary fact using only binary graph edges.

That means “hypergraphs handle n-ary facts directly” should be treated as **prior art**, not as the novelty of this project.

The interesting contribution of this new project should instead be:

- a compact semantic kernel;
- deterministic multi-target compilation;
- explicit capability/loss analysis;
- semantic round-trip measurement;
- compiler-style normalization;
- possibly a pleasant textual DSL.

## 3.5 Morgante's “Infinity Fountain” claim: do not make it a v1 premise

Morgante argues that the ORM metamodel can function as a very general meta-metamodel capable of storing and variably interpreting many modeling languages, and connects this to first-order logic, finite model theory, and Ehrenfeucht–Fraïssé games.

Treat this as an ambitious research direction, **not as a requirement to validate before building useful software**.

The new project should not depend on claims such as:

- “this is a universal metamodel”;
- “all conceptual languages can be losslessly represented”;
- “arbitrary databases can round-trip”;
- “equivalent interpretations can always be recovered”.

Those claims create enormous proof and engineering burdens and are unnecessary for a useful compiler.

Build the small falsifiable thing first.

---

# 4. Recommended synthesis

## 4.1 Semantic layer: ORM-inspired fact model

The canonical semantic objects should be something close to:

```text
Model

ObjectType
    EntityType
    ValueType
    ObjectifiedFactType

FactType

Role

Reading

Constraint
```

This is the authoritative model.

Do not make `Vertex` and `Hyperedge` the primary semantic vocabulary exposed throughout the compiler.

## 4.2 IR layer: role-aware incidence/hypergraph

The normalized compiler IR may encode the semantic model as:

```text
object-type nodes
fact-type nodes/edges
role incidence objects
constraint nodes/records
reading records
```

A fact type with n roles is equivalent to an n-ary typed relation.

This gives the implementation the uniformity that motivated the original hypergraph idea without pretending that “hypergraph” itself supplies all the needed semantics.

## 4.3 Backend layer: projections of the semantic model

Each target is a projection from the semantic model into a target schema language.

Targets should be called **schema targets** rather than all being called “database backends”, because GraphQL SDL is not a database system.

Start with:

- PostgreSQL schema target;
- MongoDB schema target;
- GraphQL schema/API target.

Each target should publish a capability table and a loss/enforcement report.

---

# 5. Formal semantic kernel

A clean mathematical model will prevent many accidental ambiguities.

For an interpretation/population `I`:

## 5.1 Object types

Each object type `T` has a finite domain:

```text
T^I = set of instances/values of T
```

Entity types and value types may have different operational treatment, but both can play roles.

## 5.2 Fact types

A fact type:

```text
F(r1:T1, r2:T2, ..., rn:Tn)
```

is interpreted as a set of tuples:

```text
F^I ⊆ T1^I × T2^I × ... × Tn^I
```

Each role is a typed argument position.

This is the conceptual heart of the project.

## 5.3 Facts are set-like by default

By default, the same role tuple represents one fact.

For example:

```text
Membership(person, group)
```

cannot occur eleven separate times as eleven distinct facts unless the model introduces an identity-bearing occurrence/object.

This resolves the exact issue the current `group_interactions` example encountered.

If repeated occurrences matter, model an event/object:

```text
InteractionOccurrence
    participant_a: User
    participant_b: User
    group: Group
    happened_at: Timestamp
```

or objectify an underlying fact and give occurrences identity.

Do **not** silently switch fact populations from sets to bags/multisets.

---

# 6. Fields should be syntax sugar, not a second semantic system

This is one of the most important recommendations.

Strict fact-oriented modeling does not need “attributes” as a separate conceptual primitive. A field such as:

```text
Person {
    email: Email
}
```

can desugar into a fact type:

```text
PersonHasEmail(
    person: Person,
    email: Email
)
```

with uniqueness and mandatory constraints determined by the surface syntax.

For example:

```text
email: Email
```

could mean:

- each `Person` has exactly one `Email`;
- modeled as a binary fact;
- uniqueness on the `person` role;
- mandatory participation for `Person`.

While:

```text
bio: Text?
```

could mean:

- each `Person` has at most one `Text` bio;
- participation is optional.

This has several advantages:

1. the semantic kernel stays uniform;
2. nullability becomes conceptual absence/presence rather than prematurely becoming SQL `NULL`;
3. fields can be mapped differently by different targets;
4. ORM constraints apply uniformly;
5. schema transformations remain possible.

The surface language may absolutely keep convenient field blocks. They simply should not survive normalization as a fundamentally different kind of semantic fact.

---

# 7. Relationship fields should desugar through objectification

The same principle applies to:

```text
fact Employment(
    employee: Person,
    employer: Company
) {
    salary: Money
    startDate: Date
}
```

The normalized model should not need “edge attributes” as a special primitive.

Instead it can conceptually become:

```text
FactType Employment(
    employee: Person,
    employer: Company
)

ObjectifiedFactType EmploymentRecord objectifies Employment

FactType EmploymentHasSalary(
    employment: EmploymentRecord,
    salary: Money
)

FactType EmploymentHasStartDate(
    employment: EmploymentRecord,
    startDate: Date
)
```

The source language can keep the compact syntax while the semantic core remains fact-based.

This is also the right place to distinguish:

- a fact that is simply true;
- a relationship that deserves object identity;
- an event/occurrence that may happen multiple times.

Do not solve those cases with ad-hoc uniqueness exceptions.

---

# 8. Objectification should be an early feature

Objectification is more important than adding a fourth output target.

It solves a deep modeling problem:

> When do I treat a relationship instance as an object that may itself play roles?

Example:

```text
Person worksFor Company
```

may be enough if employment is simply a relation.

But if the domain needs:

- salary;
- employment number;
- start/end dates;
- status;
- manager;
- contract documents;
- multiple employment episodes between the same person and company;

then an identity-bearing `Employment` object is justified.

This should be modeled explicitly rather than inferred from the presence of fields.

The compiler may provide ergonomic sugar that automatically objectifies when necessary, but the normalized semantic model should make the decision explicit.

---

# 9. Identification/reference schemes

Entity identity should be conceptual, not merely “whatever became a SQL primary key”.

Support a small identification model early.

Example:

```text
entity Person identifiedBy personId: PersonId
```

Normalized conceptually:

```text
Person has PersonId
```

plus:

- uniqueness of `PersonId`;
- mandatory participation;
- preferred identification designation.

Later support compound identification:

```text
entity Employee identifiedBy (firstName, lastName)
```

Do not equate names in the DSL with stable semantic identity.

Internally every model element should have a stable compiler ID distinct from its display name.

For v0, deterministic IDs derived from qualified names/role paths are acceptable. For future refactoring and migration support, explicit persistent IDs should be considered.

---

# 10. Readings are worth including surprisingly early

A fact type should optionally carry a reading:

```text
fact Stocking(
    part: Part,
    bin: Bin,
    warehouse: Warehouse
)
reading "{part} is in {bin} in {warehouse}"
```

Why bother?

Because readings provide:

- a human validation surface;
- a way to distinguish semantic intent from target structure;
- better diagnostics;
- future natural-language verbalization;
- a path toward user-facing model review;
- clearer role semantics.

The compiler does not need a full ORM verbalization engine in v1.

One canonical reading per fact type is enough to begin.

---

# 11. Constraints: separate three different notions

The current prototype has a useful “passed / violated / unevaluated / not applicable” instinct, but the next project should separate three layers that are currently too easy to conflate.

## 11.1 Domain/model constraints

These are statements about valid populations of the conceptual model.

Examples:

- uniqueness;
- mandatory participation;
- frequency;
- value ranges;
- subset/equality/exclusion;
- ring constraints;
- identification constraints.

These are ORM-style semantics.

## 11.2 Model-shape / IR analysis rules

These are properties of the schema representation itself.

Examples:

- every fact has arity 2;
- model graph is connected;
- no unused object types;
- no anonymous roles;
- all readings cover every role;
- no unreachable declarations.

The current `uniform(2)` and `connected` fit better here.

They should not be presented as the same kind of constraint as “each Person has at most one birthDate”.

Consider placing them under:

```text
lint { ... }
```

or:

```text
analysis { ... }
```

rather than the domain constraint namespace.

## 11.3 Target enforcement status

This is a compiler result, not a model truth value.

For each domain constraint and target, produce one of:

```text
NATIVE_ENFORCED
EMULATED_ENFORCED
REPRESENTED_NOT_ENFORCED
METADATA_ONLY
UNSUPPORTED
```

Optionally add:

```text
LOSSY_DROPPED
```

but make that a compile error by default.

This produces a much clearer report than one overloaded status field.

---

# 12. Important semantic warning: “symmetric” currently means two different things

The existing prototype uses syntax such as:

```text
symmetric(a, b)
```

to mean:

```text
(Alice, Bob) and (Bob, Alice) are the same fact
```

and canonicalizes one ordering.

That is **not necessarily the same thing** as the standard relational/ORM notion of a symmetric binary predicate:

```text
if R(a, b) then R(b, a)
```

Those semantics differ.

The new project should distinguish them explicitly.

Recommended vocabulary:

```text
unordered(a, b)
```

or:

```text
equivalentRoles(a, b)
```

for “these role positions are quotient-equivalent under permutation”.

Reserve:

```text
symmetric
```

for the logical ring constraint if standard ORM semantics are desired.

This should be fixed early because it changes instance semantics and round-trip behavior.

---

# 13. “Directed” is probably not a domain constraint in this model

A fact-oriented predicate already has named roles and readings.

For:

```text
ParentOf(parent: Person, child: Person)
```

the role distinction provides semantic orientation.

Calling the fact type “directed” is more naturally a graph-projection property than a conceptual population constraint.

Recommendation:

- remove `directed` from the core ORM-style constraint language;
- if useful, retain it as a graph-target annotation or analysis property.

Do not force graph vocabulary into the semantic core when role semantics already solve the problem.

---

# 14. Role identity, order, and repeated player types

Roles need stable identity.

Example:

```text
fact Transfer(
    sender: Account,
    receiver: Account,
    asset: Asset
)
```

Both first roles are played by `Account`, but they are different roles.

Represent at least:

```text
Role {
    id
    factTypeId
    name
    playerObjectTypeId
    presentationOrdinal
}
```

Important distinction:

- role **identity/name** is semantic;
- role **ordinal** may be presentation/reading order;
- do not infer identity solely from ordinal;
- do not infer role name solely from object type.

This is precisely where a simple “hyperedge contains a set of vertex types” representation fails.

---

# 15. Proposed normalized semantic data structures

Language-neutral sketch:

```text
Model {
    id: ModelId
    name: String
    objectTypes: Map<ObjectTypeId, ObjectType>
    factTypes: Map<FactTypeId, FactType>
    constraints: Map<ConstraintId, Constraint>
    readings: Map<ReadingId, Reading>
}

ObjectType =
    EntityType {
        id
        name
        preferredIdentifier?
    }
  | ValueType {
        id
        name
        scalarKind?
        valueConstraint?
    }
  | ObjectifiedFactType {
        id
        name
        factTypeId
        preferredIdentifier?
    }

FactType {
    id
    name
    roleIds: [RoleId]
    readingIds: [ReadingId]
}

Role {
    id
    factTypeId
    name?
    player: ObjectTypeId
    presentationOrdinal: Int
}

Reading {
    id
    factTypeId
    template
    roleOrder: [RoleId]
}

Constraint =
    Uniqueness {
        roles: [RoleId]
    }
  | Mandatory {
        role: RoleId
    }
  | Frequency {
        roles: [RoleId]
        min?
        max?
    }
  | ValueConstraint {
        objectType: ValueTypeId
        domain
    }
  | PreferredIdentifier {
        objectType: ObjectTypeId
        rolePath / constraintRef
    }
  | Subset {
        fromRoleSequence
        toRoleSequence
    }
  | Equality {
        roleSequences
    }
  | Exclusion {
        roleSequences
    }
  | RingConstraint {
        factTypeId
        kind
    }
  | UnorderedRoleGroup {
        factTypeId
        roles
    }
```

Surface `Field` syntax should disappear during desugaring/normalization.

Relationship-field syntax should normalize through objectification.

---

# 16. Hypergraph / incidence representation

A useful derived IR can be modeled as a bipartite incidence graph:

```text
ObjectTypeNode <---- RoleIncidence ----> FactTypeNode
```

For a ternary fact:

```text
Part -------- role(part) -------\
Bin  -------- role(bin) --------- FactType Stocking
Warehouse --- role(warehouse) ---/
```

This is equivalent to a typed hyperedge but preserves:

- repeated player types;
- role labels;
- role identity;
- order/readings;
- role-scoped constraints.

For instance data, a fact instance may similarly be represented as an n-ary incidence object connecting object instances.

Do not rely on a simple set-valued hyperedge implementation if it erases repeated or distinct incidences.

---

# 17. Surface DSL direction

Keep the DSL compact and approachable.

A possible style:

```text
model WarehouseModel {

    value PartNo: String
    value BinNo: String
    value WarehouseCode: String
    value Money: Decimal
    value Date: Date

    entity Part identifiedBy partNo: PartNo
    entity Bin identifiedBy binNo: BinNo
    entity Warehouse identifiedBy code: WarehouseCode
    entity Person identifiedBy id: UUID
    entity Company identifiedBy id: UUID

    fact Stocking(
        part: Part,
        bin: Bin,
        warehouse: Warehouse
    )
    reading "{part} is in {bin} in {warehouse}"
    unique(part, bin, warehouse)

    fact Employment(
        employee: Person,
        employer: Company
    ) {
        salary: Money
        startDate: Date
    }
    reading "{employee} works for {employer}"
}
```

The parser may accept this convenient syntax.

Normalization should produce:

- explicit value/object types;
- explicit fact types;
- explicit roles;
- explicit identification facts/constraints;
- objectification of `Employment`;
- salary/start-date facts about the objectified employment.

The DSL is allowed to be friendlier than the canonical semantic model.

---

# 18. Target compiler architecture

Every target adapter should implement something conceptually like:

```text
TargetAdapter {
    capabilities()
    analyze(model) -> CapabilityReport
    emit(model, options) -> GeneratedArtifacts
    read?(artifact) -> RecoveryResult
}
```

## 18.1 Capability report

For every semantic feature encountered, report:

```text
feature
sourceElement
target
status
generatedMechanism?
reason?
```

Example:

```text
constraint: Uniqueness(employee, employer)
target: postgres
status: NATIVE_ENFORCED
mechanism: UNIQUE(employee_id, employer_id)
```

Example:

```text
constraint: SomeExternalSubsetConstraint
target: graphql
status: REPRESENTED_NOT_ENFORCED
reason: GraphQL SDL has no native enforcement semantics
```

## 18.2 Fail closed on silent loss

Default behavior:

```text
if semantic feature would be silently dropped:
    compilation fails
```

Allow an explicit:

```text
--allow-loss
```

mode only if a loss manifest is emitted.

This is a stronger evolution of the existing “say what is not checked” principle.

---

# 19. PostgreSQL mapping strategy

PostgreSQL should be the first serious target.

Do not optimize aggressively at first.

Create one deterministic canonical relational mapping.

Possible rules:

1. Entity/objectified types receive tables when needed.
2. Identification becomes primary/unique keys.
3. Simple object-value facts may be absorbed as columns if safe.
4. Optional fact participation may map to nullable columns only after the semantic decision is known.
5. n-ary fact types map to relationship tables with one FK per role.
6. fact/objectification fields map according to the normalized objectification model.
7. uniqueness constraints become unique constraints/indexes where expressible.
8. value constraints become CHECK/domain constraints where feasible.
9. unsupported conceptual constraints are reported explicitly.

Important:

There may be multiple relational schemas that are semantically equivalent.

The compiler does not need to find the globally optimal schema.

It needs a deterministic, documented mapping.

---

# 20. MongoDB mapping strategy

MongoDB should be the second target because it forces different structural choices.

Initially prefer explicit references over clever embedding so that the mapping is easier to reason about and round-trip.

Use:

- `$jsonSchema` for document shape/types;
- collection-level validation mechanisms for cross-field expressions where supported;
- unique indexes for uniqueness where expressible;
- explicit reference fields for role targets.

Do not silently infer an embedding strategy from cardinality until the semantic model and target capability analysis are mature.

Embedding is an optimization/schema-design decision, not the conceptual semantics.

---

# 21. GraphQL mapping strategy

Treat GraphQL as a **schema/API projection**, not as another persistence backend.

The GraphQL adapter may generate:

- object types;
- fields;
- relationship types;
- nullability;
- comments/descriptions/directives for non-enforced semantics.

But its capability report must state that SDL by itself does not enforce many population constraints.

A reverse GraphQL reader should only be added if enough source semantics are actually encoded.

Do not claim reverse recovery merely because GraphQL contains type references.

Ask precisely what is recoverable:

- object type?
- vertex-set identity?
- fact type identity?
- role identity?
- original fact arity?
- objectification?
- constraints?
- readings?

---

# 22. Round-trip architecture

Round-tripping should be a first-class test harness, not a marketing claim.

There are three different round-trip goals.

## 22.1 Source syntax round trip

```text
source
  -> parse
  -> normalize
  -> canonical print
  -> parse
  -> normalize
```

Invariant:

```text
normalize(parse(print(model))) == normalize(model)
```

This should be implemented first.

## 22.2 Self-generated target semantic round trip

For target `T`:

```text
model
  -> emit_T
  -> read_T
  -> recoveredModel
```

The correct invariant is not always:

```text
recoveredModel == model
```

Instead define:

```text
recoveredModel == projectRecoverable_T(model)
```

and separately report:

```text
losses_T(model)
```

If the compiler intentionally emits metadata sufficient for full recovery, then the stronger equality may hold.

## 22.3 Cross-target recovery agreement

For the subset both readers support:

```text
read_postgres(emit_postgres(model))
    ==
read_mongo(emit_mongo(model))
```

after canonical normalization.

This is an excellent property test and should be retained from the current prototype.

---

# 23. Pure-target mode versus round-trip metadata mode

Do not hide critical source semantics forever in ordinary source-code comments.

Support two modes.

## 23.1 Pure target mode

Emit only what is naturally represented by the target.

Then measure exactly what can be recovered.

This is useful research because it tells us what the target schema itself preserves.

## 23.2 Metadata-preserving mode

Emit namespaced metadata specifically for semantic recovery.

Possible mechanisms:

- PostgreSQL `COMMENT ON` metadata;
- a reserved metadata schema/table;
- a sidecar manifest;
- MongoDB namespaced metadata document/collection;
- GraphQL descriptions/directives where appropriate.

A sidecar artifact could look like:

```text
model.pg.sql
model.pg.semantic.json
```

The compiler should clearly distinguish:

```text
native target semantics
```

from:

```text
compiler-carried recovery metadata
```

Do not claim that target X inherently preserves information that only survives because our compiler carried it through metadata.

---

# 24. Arbitrary database import is a later, different product

A reader for:

```text
our model -> our PostgreSQL -> our reader
```

is tractable.

A reader for:

```text
arbitrary production PostgreSQL -> exact conceptual model
```

is underdetermined.

Three foreign keys in a table could mean:

- one ternary fact;
- an entity with three independent references;
- an objectified fact;
- denormalized data;
- a historical artifact;
- an implementation optimization.

The database structure alone may not decide.

If arbitrary import is added later, represent uncertainty explicitly.

For example:

```text
RecoveredCandidate {
    interpretation
    evidence
    provenance
    confidence / unresolved status
}
```

Prefer user-assisted reverse engineering over false certainty.

---

# 25. Sample populations should become a validator tool

ORM uses sample populations as part of conceptual validation.

This could become a powerful part of the project.

Example:

```text
sample {
    Stocking(part123, binH1, warehouseSydney)
}
```

Use sample populations to:

- demonstrate fact arity;
- test uniqueness violations;
- test mandatory/frequency constraints;
- generate concrete diagnostics;
- validate compiler semantics.

This is more meaningful than checking only structural graph properties.

Do not confuse sample population validation with querying a real database.

---

# 26. Validation and diagnostics

Diagnostics should always name:

- model element;
- roles involved;
- source span;
- semantic rule;
- why it failed or could not be evaluated;
- target consequence if relevant.

Avoid:

```text
constraint failed
```

Prefer:

```text
Uniqueness constraint Stocking(part, bin, warehouse) violated:
sample facts #3 and #8 have the same role tuple.

Source:
  fact Stocking(...)
  unique(part, bin, warehouse)
```

For target compilation:

```text
Constraint ExternalSubsetConstraint(...) is valid in the source model
but cannot be enforced by GraphQL SDL.
Status: REPRESENTED_NOT_ENFORCED
```

---

# 27. Recommended project phases

## Phase 0 — write the semantic contract

Before writing emitters, freeze a small semantic spec.

Define:

- object type;
- value/entity type;
- fact type;
- role;
- fact population;
- uniqueness;
- mandatory participation;
- reading;
- identification;
- objectification;
- unordered-role semantics.

Write examples and counterexamples.

## Phase 1 — parser, desugaring, normalizer, printer

Deliver:

```text
DSL -> AST -> normalized semantic model -> canonical DSL
```

Tests:

```text
parse(print(normalize(x))) == normalize(x)
```

No databases required.

## Phase 2 — semantic validator

Implement:

- role/type resolution;
- uniqueness definitions;
- mandatory role definitions;
- identification checks;
- objectification well-formedness;
- reading coverage;
- sample populations.

## Phase 3 — PostgreSQL target

Implement the smallest deterministic relational mapping.

Generate a capability report with every compilation.

## Phase 4 — PostgreSQL narrow reader

Read only the exact canonical subset generated by Phase 3.

Measure recoverability.

Do not generalize the parser prematurely.

## Phase 5 — MongoDB target + reader

Repeat the process with a structurally different target.

Add cross-target recovery equality tests.

## Phase 6 — GraphQL projection

Generate GraphQL as an API/type schema.

Make target limitations explicit.

Do not add a reverse reader until there is a concrete recovery contract.

## Phase 7 — richer ORM constraints

Add gradually:

- frequency;
- value constraints;
- subset;
- equality;
- exclusion;
- subtyping;
- selected ring constraints.

Do not implement the complete ORM constraint catalog in one shot.

## Phase 8 — metamodel persistence / self-hosting experiments

Only after the compiler is useful should the project explore:

- storing the semantic model in its own metamodel;
- multiple models / symbols / concept dictionaries;
- higher-order/meta-model use;
- variable interpretations.

This is where Morgante’s broader Boston/Infinity-Fountain ideas become relevant.

---

# 28. Testing strategy

This project should be unusually test-heavy.

## 28.1 Unit tests

For every semantic construct.

## 28.2 Golden tests

Canonical source:

```text
input.dsl
expected.normalized.dsl
expected.pg.sql
expected.mongo.js
expected.graphql
expected.capabilities.json
```

## 28.3 Property tests

Examples:

```text
normalize(normalize(m)) == normalize(m)
```

```text
parse(print(m)) ~= m
```

```text
read_pg(emit_pg(m)) == recoverable_pg(m)
```

```text
read_mongo(emit_mongo(m)) == recoverable_mongo(m)
```

```text
read_pg(emit_pg(m)) == read_mongo(emit_mongo(m))
```

for the mutually recoverable subset.

## 28.4 Mutation tests

Mutate:

- role order;
- role names;
- repeated player types;
- uniqueness role subsets;
- optionality;
- objectification;
- constraint declarations.

The tests should fail if any mutation is silently erased.

## 28.5 Determinism tests

Repeated builds must produce byte-identical artifacts given the same normalized model and compiler version.

---

# 29. Suggested repository shape

```text
/
  docs/
    semantics.md
    orm-prior-art.md
    target-capabilities.md
    roundtrip-contract.md

  examples/
    warehouse/
    employment/
    social/
    repeated-role-types/
    objectification/

  src/
    syntax/
      lexer
      parser
      ast

    semantics/
      model
      ids
      object_types
      fact_types
      roles
      readings
      constraints
      objectification
      populations
      validation

    normalize/
      desugar_fields
      desugar_relationship_fields
      identifiers
      canonical_order

    ir/
      incidence
      indexes

    targets/
      postgres/
        capabilities
        emitter
        reader
      mongo/
        capabilities
        emitter
        reader
      graphql/
        capabilities
        emitter

    reporting/
      diagnostics
      capability_report
      loss_manifest

  tests/
    unit/
    golden/
    property/
    roundtrip/
```

Keep the semantic model independent of all three targets.

No target-specific type should leak into `semantics/`.

---

# 30. First serious example: Stocking

Use the classic ternary example because it exercises the central point cleanly.

```text
fact Stocking(
    part: Part,
    bin: Bin,
    warehouse: Warehouse
)
reading "{part} is in {bin} in {warehouse}"
unique(part, bin, warehouse)
```

Why this example?

- arity 3;
- distinct role types;
- simple uniqueness;
- clear natural-language reading;
- direct relational mapping;
- awkward binary-edge graph mapping;
- possible later objectification as `StockedItem`.

Then objectify:

```text
objectify Stocking as StockedItem
```

and attach another fact:

```text
fact StockedItemHasQuantity(
    stockedItem: StockedItem,
    quantity: Quantity
)
```

This tests whether relationship/object duality is handled cleanly.

---

# 31. Second serious example: repeated role player type

```text
fact Recommendation(
    recommender: Person,
    candidate: Person,
    job: Job
)
```

This proves the IR cannot collapse incidences just because two roles share the same player type.

Add:

```text
reading "{recommender} recommends {candidate} for {job}"
```

Then test reversal:

```text
Alice recommends Bob for JobX
```

must not equal:

```text
Bob recommends Alice for JobX
```

unless the model explicitly declares role equivalence/unordered semantics.

---

# 32. Third serious example: unordered versus symmetric

Create two different constructs.

## Unordered partnership

```text
fact Partnership(
    a: Person,
    b: Person
)
unordered(a, b)
```

Semantics:

```text
Partnership(Alice, Bob)
==
Partnership(Bob, Alice)
```

One fact modulo permutation.

## Symmetric recognition

```text
fact Knows(
    knower: Person,
    known: Person
)
symmetric
```

Semantics:

```text
Knows(Alice, Bob)
=> Knows(Bob, Alice)
```

These should have different normalized semantics and different target enforcement strategies.

This example will prevent an early conceptual bug.

---

# 33. What not to build first

Do not begin with:

- a graphical editor;
- arbitrary PostgreSQL reverse engineering;
- arbitrary MongoDB reverse engineering;
- a universal metamodel claim;
- query compilation;
- data migration;
- resolver generation;
- schema optimization;
- automatic embedding choices;
- an ontology reasoner;
- higher-order logic;
- an Ehrenfeucht–Fraïssé engine;
- a hypergraph database runtime;
- ten output targets.

Those are all attractive distractions.

The first product is a semantic compiler.

---

# 34. Definition of done for v0.1

v0.1 should support:

- entity types;
- value types;
- binary and n-ary fact types;
- named roles;
- repeated player types;
- one canonical reading;
- simple and compound identifiers;
- uniqueness;
- mandatory/optional participation;
- unordered role groups;
- objectification;
- ergonomic field sugar;
- relationship-field sugar desugared through objectification;
- sample populations for basic validation;
- deterministic PostgreSQL generation;
- PostgreSQL capability report;
- narrow PostgreSQL read-back;
- canonical semantic round-trip test;
- no silent loss.

If all of that works, **then** add MongoDB.

A one-target compiler with a correct semantic kernel is more valuable than a three-target generator built on ambiguous semantics.

---

# 35. Definition of done for v0.2

Add:

- MongoDB generator;
- MongoDB reader for compiler-generated subset;
- cross-target recovered-model equality;
- richer value constraints;
- frequency constraints;
- loss manifest;
- pure-target vs metadata-preserving round-trip modes.

---

# 36. Long-term possibilities

If the semantic kernel proves useful, the project could grow naturally into:

## Semantic schema diff / migrations

Diff two conceptual models rather than diffing generated DDL.

Then plan target-specific migrations.

## Multiple front-end syntaxes

The same semantic model could be populated by:

- textual DSL;
- ORM diagram import;
- JSON/YAML;
- a future graphical editor.

## Multiple interpretations

A model might produce:

- relational schema;
- document schema;
- GraphQL API;
- JSON Schema;
- RDF/SHACL;
- property graph schema.

This is a practical, testable version of “variable interpretation” without claiming universal equivalence.

## Metamodel-as-data

Persist models using the same fact-oriented metamodel.

This is the stage where the Boston metamodel becomes especially relevant.

## Assisted reverse engineering

Import arbitrary schemas into an **uncertain recovered model** with provenance and user decisions.

Do not call this lossless unless it actually is.

---

# 37. Novelty / positioning

Do not position the project as:

> Hypergraphs can represent n-ary relations.

That is old.

Do not position it as:

> ORM but better.

ORM has decades of mature semantics.

A credible positioning is:

> A small fact-oriented semantic compiler whose normalized representation is a role-aware incidence/hypergraph IR, with deterministic multi-target schema generation, explicit enforcement/loss accounting, and measurable semantic round trips.

Potentially interesting engineering/research contributions:

1. capability-aware compilation of fact-model constraints;
2. first-class loss manifests;
3. round-trip measurement across heterogeneous schema targets;
4. a compact role-aware IR;
5. pleasant textual sugar that normalizes to a fact-oriented kernel;
6. cross-target semantic equality tests;
7. distinguishing native target semantics from compiler-carried recovery metadata.

---

# 38. Non-negotiable engineering principles

1. **No silent inference.**
   If the compiler does not know, represent unknown/unevaluated.
2. **No silent semantic loss.**
   Fail or emit an explicit loss report.
3. **Names are not semantics.**
   Roles/types need identity independent of naming conventions.
4. **Target structure is not conceptual intent.**
   Reverse readers should not invent intent absent evidence.
5. **Hypergraph structure is not enough.**
   Roles/incidences are first-class.
6. **Surface syntax is not the normalized model.**
   Desugar conveniences aggressively.
7. **Target-specific enforcement is distinct from model validity.**
8. **Round trip means semantic equality under a stated projection.**
   Never use the phrase without specifying the equality contract.
9. **GraphQL is a schema/API target, not a database backend.**
10. **Start narrow and falsifiable.**
    Expand only when tests force the next abstraction.

---

# 39. Questions the implementation must answer explicitly

Before implementation gets deep, write decisions for these:

1. Are fact populations sets only?
2. How are repeated occurrences modeled?
3. What exactly does objectification mean in the internal model?
4. Is field syntax purely sugar?
5. Are relationship fields always objectification sugar?
6. What is the distinction between `unordered` and logical `symmetric`?
7. Does role ordinal have semantic meaning, presentation meaning, or both?
8. How are stable IDs assigned?
9. Which constraints are v0.1 semantic primitives?
10. Which old graph constraints move into a lint/analysis namespace?
11. What counts as semantic equality of two normalized models?
12. What metadata is allowed in “pure target” mode?
13. What metadata is allowed in “round-trip” mode?
14. Does a reverse reader return one model or a recovery result with losses/provenance?
15. Does compilation fail on unsupported semantics by default?
16. What is the canonical relational mapping?
17. Which transformations are allowed before emission?
18. How are source locations retained through desugaring for diagnostics?

Do not leave these implicit in code.

---

# 40. Suggested first coding-agent assignment

Before adding any new article/demo, implement the semantic nucleus.

**Task:**

Create a fresh package/module containing only:

```text
Model
EntityType
ValueType
FactType
Role
Reading
Constraint::Uniqueness
Constraint::Mandatory
Constraint::UnorderedRoleGroup
Objectification
SampleFact
```

Then implement:

1. stable IDs;
2. constructors with invariant checks;
3. canonical serialization;
4. semantic equality;
5. role-aware incidence index;
6. examples for:
   - `Part is in Bin in Warehouse`;
   - `Person recommends Person for Job`;
   - unordered partnership;
   - objectified employment;
7. property tests proving repeated player types remain distinct;
8. tests proving unordered and symmetric semantics are not conflated;
9. tests proving normalization is idempotent.

Do **not** write PostgreSQL code until this package feels boring and precise.

Then add a very small source parser and pretty-printer around it.

Only after:

```text
parse -> normalize -> print -> parse -> normalize
```

is stable should PostgreSQL generation begin.

---

# 41. Source / prior-art basis for this brief

This design brief was informed by:

- the evolving local draft **“Hypergraphs as an intermediate representation for database translation”** reviewed during this project;
- Victor Morgante, **“The Importance of the Metamodel of Object-Role Modeling”**;
- Victor Morgante, **“The Boston Object-Role Modeling Metamodel — Pt 1 — Concepts”**;
- Victor Morgante, **“The Boston Object-Role Modeling Metamodel — Pt 3 — Entity Types”**;
- Victor Morgante, **“The Boston Object-Role Modeling Metamodel — Pt 4 — Fact Types”**;
- Victor Morgante, **“Fact Type Readings in Object-Role Modeling”**;
- Victor Morgante, **“Reification in Object-Role Modeling”**;
- Victor Morgante, **“N-Ary Relationships in Graph Databases”**;
- Terry Halpin / ORM.net material describing ORM as fact-oriented conceptual modeling, roles, constraints, relational mapping, verbalization, and objectification;
- Terry Halpin, **“Objectification”**.

Important distinction:

- The **ORM/fact-type/role/constraint/objectification** material is established prior art.
- The proposed compiler architecture, loss-manifest design, IR layering, build sequence, and recommendations in this document are **engineering synthesis/recommendations**, not claims made by Morgante or Halpin.
- Morgante’s “Infinity Fountain” / very broad meta-metamodel interpretation is an ambitious thesis and should not be treated as established engineering fact or as a v1 dependency.

---

# 42. Bottom line for the coding agent

Start over at the semantic layer, not at the emitter layer.

Build:

```text
FACT-BASED SEMANTICS
        |
        v
ROLE-AWARE INCIDENCE IR
        |
        v
CAPABILITY-AWARE TARGET COMPILERS
        |
        v
EXPLICIT LOSS / ENFORCEMENT REPORTS
        |
        v
NARROW, MEASURABLE ROUND TRIPS
```

If the project does this well, “hypergraph” becomes a useful implementation insight rather than a branding burden, and ORM becomes a source of mature semantics rather than a giant framework that must be copied in full.

That is the most powerful clean start.
