# v0.6 milestone — metamodel as data / semantic self-hosting

## Goal

Make the normalized Factgraph metamodel representable **as a Factgraph model**, then make normalized Factgraph models representable **as populations of that metamodel**.

The milestone is successful only if the representation is executable and measurable:

```text
subject model
    -> encode as metamodel population
    -> validate that population using ordinary Factgraph validation
    -> decode population
    -> compare normalized semantics
```

For the compiler envelope the stronger invariant is:

```text
manifest(subject) == manifest(decode(encode(subject, envelope=true)))
```

The metamodel must also pass the same process when the subject is the metamodel itself.

## Narrow self-hosting claim

v0.6 claims:

> The schema of normalized Factgraph models is itself a Factgraph model, and a Factgraph model can describe its own normalized semantic/manifest structure as a population of that model.

It does **not** claim:

- that Python source code is generated from the metamodel;
- that the parser, normalizer, emitters, or validators are bootstrapped from their own data;
- that Factgraph is a universal metamodel for arbitrary modeling languages;
- that metamodel closure proves lossless arbitrary database reverse engineering;
- that the Boston ORM metamodel has been copied completely.

## Layers

v0.6 deliberately separates two reification layers.

### Semantic core population

Carries exactly the information used by `Model.semantic_dict()`:

- model identity/name;
- object types and kinds;
- value scalar kinds;
- objectification links;
- fact types;
- roles, role order, players, and names;
- readings and reading-role order;
- all normalized constraints;
- analysis declarations.

Required invariant:

```text
subject.semantically_equal(decode(core_population))
```

### Compiler-envelope population

Adds non-conceptual compiler/source data:

- sample populations;
- field projection hints used to reconstruct ergonomic field syntax;
- source-line provenance notes.

Required invariant:

```text
subject.manifest_dict() == decode(envelope_population).manifest_dict()
```

This distinction prevents compiler provenance from being mislabeled as conceptual semantics.

## Metamodel shape

The canonical metamodel uses ordinary Factgraph object/value/fact types.

Examples of metamodel facts:

```text
MM_ModelObjectType(model, objectType)
MM_ObjectTypeName(objectType, name)
MM_ObjectTypeKind(objectType, kind)

MM_ModelFactType(model, factType)
MM_FactTypeName(factType, name)
MM_FactRole(factType, role, ordinal)
MM_RoleName(role, name)
MM_RolePlayer(role, objectType)

MM_ModelConstraint(model, constraint)
MM_ConstraintKind(constraint, kind)
MM_ConstraintRole(constraint, role, ordinal)
```

Constraint optionality is represented by fact absence rather than null-valued metamodel fields.

## Commands

Emit the canonical metamodel and self-description evidence:

```bash
factgraph metamodel --out-dir artifacts/metamodel_self
```

Reify any ordinary model:

```bash
factgraph reify examples/richer_constraints.fg \
  --out-dir artifacts/reified_richer_constraints
```

Ordinary `factgraph build` also writes a `metamodel/` evidence directory for the built model.

## Required files

`factgraph metamodel` writes:

```text
metamodel.fg
semantic.json
manifest.json
validation.json
incidence.json
self.population.json
self.populated.metamodel.manifest.json
self_host.json
```

`factgraph reify` writes:

```text
core.population.json
envelope.population.json
populated.metamodel.manifest.json
recovered.semantic.json
recovered.manifest.json
recovered.normalized.fg
roundtrip.json
```

## Definition of done

- [x] canonical metamodel persisted as packaged `src/factgraph/data/factgraph_metamodel.fg`;
- [x] metamodel parsed/normalized through the ordinary Factgraph front end;
- [x] canonical metamodel print reproduces the packaged source byte-for-byte;
- [x] metamodel represented using ordinary Factgraph semantic objects;
- [x] metamodel canonical print -> parse -> normalize equality;
- [x] semantic-core population encoder/decoder;
- [x] compiler-envelope population encoder/decoder;
- [x] population validation using ordinary `validate_model`;
- [x] metamodel self-population round trip;
- [x] second self-encoding fixed-point equality;
- [x] every ordinary example reifies during normal build;
- [x] CLI file handoffs for `metamodel` and `reify`;
- [x] explicit non-universality/non-bootstrap claims.
