# Semantic portability audit

## Objective

A Factgraph audit asks whether each **source semantic obligation** remains true of populations admitted by a target representation.

The target schema is evidence, not the conclusion. A `FOREIGN KEY`, `UNIQUE`, JSON Schema rule, or TypeDB annotation is useful only insofar as it discharges a specific source obligation.

## Obligation families

The v0.10 audit kernel derives obligations for:

- source-fact set semantics;
- uniqueness;
- preferred identification;
- required fields and total participation;
- frequency bounds;
- scalar ranges/enumerations;
- unordered role groups;
- ring/symmetry constraints;
- subset/equality/exclusion;
- subtype inclusion.

Objectification is reflected through fact/object identities and relationship-owned field obligations.

## Evidence levels

### 1. Declared

The target adapter assigns a capability status and names the intended mechanism.

### 2. Structurally checked

A conformance case confirms the emitted artifact contains the expected mechanism. Structural checks are deterministic and run without a database server.

### 3. Witness tested

The obligation is associated with a discriminating population recipe and, where currently supported, concrete target statements/documents. A witness should aim to violate one obligation without relying on an unrelated violation.

### 4. Live observed

The real target executes the population. For targets that validate at transaction commit, commit is part of the observation.

Only this level changes a preserved claim into `preserved_observed` or a known gap into `weakened_observed`.

## Preservation states

- `preserved_claimed`
- `preserved_observed`
- `weakened`
- `weakened_observed`
- `metadata_only`
- `lost`
- `unknown`
- `claim_falsified`

`claim_falsified` is intentionally severe: the adapter claimed native/emulated enforcement but the live discriminating case did not behave as expected.

## Files

`factgraph audit` produces:

```text
audit.json
AUDIT_SUMMARY.json
audit.md
obligations.json
transformation_trace.json
witnesses/
targets/postgres/{verdicts.json,live.json}
targets/mongo/{verdicts.json,live.json}
targets/typedb/{verdicts.json,live.json}
```

External imports additionally produce `source_import/source.txt`, the normalized Factgraph representation, and an explicit `import_report.json`.

## Important semantic distinctions

### Mandatory participation is directional

A mandatory source role says that every player participates in the fact in that role. A non-null foreign key located on the fact row usually enforces only that every fact has a player.

### Frequency lower bounds are not total participation

Factgraph `frequency(role, min, max)` constrains multiplicity among represented facts. A lower bound greater than zero is not automatically interpreted as “every possible role player must appear.” A target cardinality construct that adds existence would therefore be a semantic mismatch.

### Unordered is not logical symmetry

`unordered(a,b)` identifies permutations as one fact. `symmetric` says `R(a,b) => R(b,a)`. A canonical ordering CHECK can implement the former but not the latter.

### Relation instance identity is not tuple-set identity

A target with first-class relation instances can still permit two distinct relation objects with identical players. That is why the TypeDB projection can faithfully represent an n-ary relation while still weakening Factgraph's mathematical fact-set semantics unless an additional identity rule is available.

## Witness conservatism

The v0.10 generator deliberately skips a concrete live witness when an unrelated mandatory rule could cause the same rejection. A missing observation is preferable to attributing a rejection to the wrong semantic cause.

This is an area for future property-based generation/shrinking and bounded solving.
