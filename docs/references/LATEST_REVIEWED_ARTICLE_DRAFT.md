Draft preview  ·  local copy, not published

# Hypergraphs as an intermediate representation for database translation

A hypergraph is a graph whose edges may join any number of vertices, not only two. An intermediate representation is the form a compiler translates through. Used as one, a hypergraph keeps relationship structure in a single form across targets that each encode it differently.

A small compiler takes that literally. It reads one file describing a model as a hypergraph and writes PostgreSQL DDL, a MongoDB collection script, and a GraphQL schema. A binary relation is the special case where an edge has two endpoints. A relation over three or more is declared the same way and compiled the same way.

Figure 1 — one ternary fact, as a binary graph and as a hypergraph

Figure 1 — one ternary fact, as a binary graph and as a hypergraph

Figure 2 — the invented node and its three edges collapsing into one hyperedge

Figure 2 — the invented node and its three edges collapsing into one hyperedge

It also reports which of the model's declared constraints hold, and separates a constraint that passed from one it could not check.

---

## The input

A model declares vertex sets and hyperedges (relations that may connect more than two vertex sets). One file carries the whole example.

```
system SocialNetwork {
    vertices users:  User  labeled String
    vertices groups: Group labeled String
    vertices posts:  Post  labeled String

    hyperedges friendships:        connect User User
    hyperedges memberships:        connect User Group
    hyperedges authorship:         connect User Post
    hyperedges group_interactions: connect User User Group
}

constraints {
    uniform(2)
    directed
    connected
}
```

Three of the four hyperedges connect two vertex sets. The fourth connects three: two users and a group. That is an interaction between two people inside a specific group. It is a single fact about three participants, not three separate pairs.

## Why the fourth line is the interesting one

A relational schema can state a three-way relation directly. One table, three foreign keys, one row per fact. The catalog exposes those foreign keys and their ordinal positions, so a tool can count them. What SQL does not record is why the three columns are there. They may be the participants of one ternary relation. They may be three independent references belonging to an entity. That is a modelling decision, and the schema keeps no place for it.

Binary-edge models cannot state it as a single edge. RDF triples and property graph edges join exactly two things. A three-way fact therefore needs an invented node: a blank node, or an `employment` vertex, carrying one edge per participant. That node is introduced to represent the relation as an object. Sometimes it deserves to be one: an employment contract has its own start date and salary. Sometimes it is only bookkeeping. The schema does not record which.

The cost is not the table. The cost is that the arity (how many things a relation connects) stops being a first-class declaration. It survives as a shape a tool can count, not as a statement the model makes.

Declaring `connect User User Group` keeps arity in the model. The generator derives the junction table from it.

Figure 3 — the same fact in four formats, and what each direction costs

Figure 3 — the same fact in four formats, and what each direction costs

## The output

`group_interactions` produces this PostgreSQL table.

```
-- hyperedge group_interactions: arity 3, connects User + User + Group
CREATE TABLE group_interactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_1_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_2_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id  UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    UNIQUE (user_1_id, user_2_id, group_id)
);
CREATE INDEX group_interactions_user_1_id_idx ON group_interactions (user_1_id);
CREATE INDEX group_interactions_user_2_id_idx ON group_interactions (user_2_id);
CREATE INDEX group_interactions_group_id_idx  ON group_interactions (group_id);
```

Repeated endpoints are numbered, not merged. Two `User` endpoints become `user_1_id` and `user_2_id`. The unique constraint spans all three columns, so the same pair interacting in two different groups is two rows.

The same declaration produces a MongoDB collection with a `$jsonSchema` validator and a compound unique index across the three references. In GraphQL it produces one type with three typed fields, reachable from both `User` and `Group`.

One line of model, three representations, no hand-written junction.

Figure 4 — one arity-3 declaration compiled to three backends

Figure 4 — one arity-3 declaration compiled to three backends

The generated text is deterministic. No timestamps, no generated identifiers, no ordering that varies between runs. Two runs of the same model produce byte-identical files, so the output diffs cleanly in version control.

## What generality buys, and what it does not

Representing many data structures as hypergraphs is neither new nor difficult. Trees, relations, and RDF triples all have standard hypergraph encodings. The equivalences are long established: Berge and Harary in graph theory, Spivak's functorial data migration in database theory. Generality of representation is cheap, and claiming it proves nothing.

The cost is equally real. A hypergraph encoding of a simple structure discards the operations that made the structure worth choosing. Vector arithmetic, relational joins, and hierarchical traversal are each natural in their own paradigm and awkward in a universal one. A general representation trades operational fit for uniformity.

The claim here is therefore narrow. The hypergraph is not offered as a better model of your data. It is offered as the form to translate *through*, where the only operation required is emitting a schema. Translation is the one task where uniformity pays and operational fit does not matter, because nothing is computed in the intermediate form.

That is why arity 3 carries the argument. It is the smallest case where the three targets need materially different encodings. The translation still succeeds into all of them.

## The constraint verdict

The model declares three constraints. The tool reports on each, and on the constraints it supports that this model did not declare.

Figure 5 — four verdict states, one per declared constraint

Figure 5 — four verdict states, one per declared constraint

`uniform(2)` requires every hyperedge to connect exactly two vertex sets. The model declares it, and `group_interactions` connects three. The verdict names the constraint, the count, and the specific hyperedge that violates it.

`connected` holds. All three vertex sets lie in one component.

`directed` is reported as **unevaluated**. The language expresses which vertex types a hyperedge connects. It does not express which way the relation runs. No structural test decides direction, so the tool reports the constraint as unchecked and states why.

`acyclic` is reported as **not applicable**. The model does not declare it, so nothing is claimed about it.

## Why four states and not two

Most validators report pass or fail. That merges two different situations: a constraint that was checked and held, and a constraint that could not be checked at all.

The merge is a reporting bug with a specific cost. A model whose constraints are half unverifiable reports the same green as a model whose constraints are all verified. The difference is exactly the information you needed.

Separating them makes the tool say something falsifiable about its own coverage. Three declared constraints, one holding, one violated, one unevaluated. You can act on that. "Two of three passed" hides which two.

## What survives the round trip

A reader recovers a model from the PostgreSQL this tool generated. It is bounded to exactly that. Reading back self-generated output is a much smaller problem than reading a database in general. It is enough to measure what the representation keeps.

Reading only executable SQL, it recovers the vertex sets and their fields, with nullability. It also recovers the hyperedge names, the endpoints in order, the participant roles and the symmetric pairs. Roles survive because they are column names. Symmetry survives because it is a `CHECK` constraint. Neither is a convention held somewhere outside the schema.

Two things do not survive. The vertex type names appear in the generated file only inside comments. A reader restricted to executable SQL sees a table called `users` and cannot know its declared type was `User`. The declared constraints are evaluated when the schema is generated and never emitted. The reader reports both rather than inventing them.

Admitting the comments recovers the type names, and re-emitting reproduces the file byte for byte.

That is the measurement the argument above needs. What the intermediate representation keeps and the schema does not is not the structure. PostgreSQL holds the structure comfortably. It is the record of why the structure is there.

The same reader exists for MongoDB, and the two agree. Rendering both recovered models back to the source language produces identical text. Two front ends that share no parser recovered the same model from two different serialisations of it.

MongoDB keeps more in its executable form than PostgreSQL does. An endpoint's target collection is a string inside the validator rather than a comment. A reader that ignores comments still finds it. The type names are lost in both, for the same reason.

## What it does not do

The scope is small and stated, so the absences are deliberate rather than pending.

Operation bodies are parsed and ignored. No complexity analysis, no resolver generation. The verdict claims nothing about operations.

No instance data is loaded, validated, or migrated. Constraints are evaluated against the declaration only. Anything that is a property of rows rather than of the model is reported unevaluated.

No database is contacted. No server, no container, no connection string, no credential. Generated schemas are text, and the tool never executes them.

Translation runs both ways for two of the three targets. PostgreSQL and MongoDB have readers. GraphQL does not, because SDL carries no endpoint targets. Reading arbitrary PostgreSQL or MongoDB is a different problem and is not attempted. This is still a source format rather than a universal exchange format, because each reader reads only what this tool wrote.

Symmetry is enforced in two of the three targets. A participant can carry a role. `connect a:User b:User symmetric(a, b)` declares the two positions interchangeable, so `(Alice, Bob)` and `(Bob, Alice)` are one fact. PostgreSQL gets `CHECK (a_id <= b_id)` beside the unique constraint, which refuses a reversed pair by name rather than reordering it quietly. MongoDB combines `$jsonSchema` with `$expr` in the collection validator. `$jsonSchema` describes one document at a time and cannot compare two of its own fields. GraphQL SDL has no built-in cross-field constraint, and a custom directive would need some server to implement it. Its schema carries a note naming the pair as declared and not enforced there. That is the same posture as an unevaluated constraint: say what is not being checked.

Vertex sets and relationships both carry fields. A block of `email: String`, `age: Int`, `bio: String?` becomes typed columns, validator properties and SDL fields. The `?` carries nullability into all three. A relationship takes the same block, so an employment edge can hold a salary and a start date. What a field cannot yet carry is a constraint of its own, such as a salary that must be positive. It also makes a hyperedge a statement that the participants are related, not a record of an occurrence. `group_interactions` says this pair interacts in this group. It cannot say they did so eleven times, because a second row would violate the generated unique constraint.

A field naming a type the language does not know is refused rather than defaulted. An earlier version guessed columns from type names, so a set called `users` acquired `name`, `email` and `created_at` that no model declared. Guessed columns are worse than absent ones, because they look declared.

## Where it is useful

The tool is worth reaching for under three conditions together. Your model has relations over more than two things. You are targeting more than one store. You want the arity and the constraints to stay declared rather than becoming a property of hand-written DDL.

Under those conditions it removes the hand-maintained junction tables and tells you which of your declared constraints are actually being enforced.

Outside them, an ORM or a migration tool covers the same ground with more features.

---

## Appendix: where the unevaluated state came from

The predecessor to this tool reported every model as disconnected. Its adjacency map was keyed on vertex set names (`users`). Its hyperedges referenced vertex type names (`User`). The lookup never matched. A membership guard around the write suppressed the `KeyError` that would have named the problem on the first iteration.

The report that resulted was internally consistent and wrong in every number. It stated a maximum degree of 0 and a density of 0.833 in the same document.

That failure is the reason the current verdict separates unchecked from passing. A validator that cannot distinguish them will eventually report the second when it means the first.