# Metamodel-as-data and semantic self-hosting

## Why this exists

The original project used an ORM-inspired semantic model implemented directly in Python. v0.6 asks a narrower version of the metamodel question raised by Object-Role Modeling work:

> Can the concepts used to describe Factgraph models themselves be represented in the same fact-oriented language?

The answer implemented here is yes for the current normalized Factgraph contract.

The important part is not that a graph-like representation is capable of encoding its own schema. That alone is unsurprising. The useful engineering property is that the encoding has explicit invariants, uses the existing validator, and decodes back to the same semantic structures.

## A metamodel is still a model

`src/factgraph/data/factgraph_metamodel.fg` remains a compatibility alias for the **current** metamodel. v0.7 stores the actual versioned contracts under `src/factgraph/data/metamodel/v1.fg` and `v2.fg`. `factgraph.metamodel.build_metamodel(version)` reads the requested packaged file through the ordinary parser and normalizer and returns an ordinary `Model`. Regression tests require canonical printing to reproduce the packaged source byte-for-byte.

It contains entity/value types such as:

```text
MM_Model
MM_ObjectType
MM_FactType
MM_Role
MM_Reading
MM_Constraint
MM_Text
MM_Integer
```

and facts such as:

```text
MM_FactRole(factType, role, ordinal)
MM_RolePlayer(role, objectType)
MM_ReadingFact(reading, factType)
MM_ConstraintTargetRole(constraint, role, ordinal)
```

The metamodel has no privileged validator or alternate execution engine. Its self-population is stored as ordinary `SampleFact` rows and checked by `validate_model()`.

## Why role order is explicit

The metamodel does not infer role identity from object-type occurrence.

For a source fact:

```text
Recommendation(recommender: Person, candidate: Person, job: Job)
```

both `Person` roles become distinct `MM_Role` instances. `MM_FactRole` stores their ordinals, while `MM_RolePlayer` stores that both are played by `Person`.

That preserves the role-aware incidence principle on which the compiler is based.

## Core semantics versus compiler envelope

Two populations are generated.

### Core

The core population represents the canonical semantic model. Exact source formatting, field sugar, and source positions are intentionally absent.

Its success criterion is semantic equality.

### Envelope

The envelope additionally carries:

- field projection hints;
- sample facts;
- source notes.

This permits exact `manifest_dict()` recovery. It is explicitly labeled compiler-envelope data rather than conceptual semantics.

## Self-description

The strongest v0.6 test uses the metamodel itself as the subject:

```text
FactgraphMetamodel
      |
      | encode
      v
population of FactgraphMetamodel
      |
      | validate + decode
      v
FactgraphMetamodel'
```

The release requires:

- no population validation errors;
- semantic equality;
- manifest equality;
- identical canonical population after encoding the decoded metamodel again.

This last check makes the representation a deterministic fixed point rather than a one-direction serialization demo.

## What this does not prove

### Not compiler bootstrapping

The Python classes `Model`, `FactType`, `Role`, etc. still implement the runtime semantics. The metamodel does not generate those classes or the parser.

### Not universal modeling-language equivalence

A language may have semantics Factgraph cannot currently state. Encoding a syntax tree is not the same as preserving its meaning.

### Not arbitrary database recovery

A PostgreSQL schema can still underdetermine conceptual intent. Metamodel closure does not change that.

### Not the complete ORM metamodel

The design is ORM-inspired and incorporates fact types, roles, readings, constraints, objectification, and identification. It does not claim complete parity with NORMA, the Boston metamodel, or every ORM constraint family.

## Why this is useful anyway

This layer creates several practical future options:

1. **versioned metamodel evolution** — the same semantic diff/migration machinery can eventually compare metamodel versions;
2. **alternate front ends** — diagram/JSON/ORM importers can target the same metamodel population contract;
3. **model repositories** — conceptual models can be stored/queryable as fact populations;
4. **higher-order tooling** — diagnostics, documentation, and transformations can inspect model structure as data;
5. **controlled research on interpretation** — broader Morgante-style “variable interpretation” experiments can be attempted without making universality a premise.

The next step should be to use this closure operationally rather than adding another layer of meta terminology.

---

## v0.7: versioning the metamodel itself

v0.6 established semantic closure for one canonical metamodel. v0.7 makes that metamodel versioned.

The package preserves the v0.6 contract as `data/metamodel/v1.fg` and introduces `v2.fg` with explicit codec/integrity facts. A population now records the metamodel version used to encode it.

Migration is performed by semantic decode/re-encode, not by guessing how old rows should be rewritten. See `V0_7_MILESTONE.md` and `MODEL_REPOSITORY.md`.
