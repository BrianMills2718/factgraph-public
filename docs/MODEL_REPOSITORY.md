# Factgraph model repository

The repository began in v0.7 as a filesystem-native store for versioned conceptual models and now supports stable identities, revision DAGs, and v0.9 filesystem collaboration.

Its purpose is to make three pieces of provenance explicit:

```text
which logical model?
which immutable model revision?
which metamodel version encoded that revision?
```

## Layout

A repository looks like:

```text
repository.json
metamodels/
  index.json
  v1/
    metamodel.fg
    semantic.json
    manifest.json
    version.json
  v2/
    ...
models/
  index.json
  <stable-directory-key>/
    model.json
    revisions/
      rev-.../
        revision.json
        source.fg
        normalized.fg
        semantic.json
        manifest.json
        validation.json
        population.core.json
        population.envelope.json
        metamodel_migration.json   # only on metamodel-migration revisions
```

`revision.json` contains hashes for the immutable revision artifacts.

Indexes are mutable pointers. Revision directories are immutable history.

## Logical model keys

Factgraph supports explicit rename-stable semantic identity tokens as well as legacy deterministic name-derived IDs. A repository still has a separate logical `model_key` so repository identity is explicit and is not inferred from model display names.

Use an explicit model key if a conceptual model may be renamed:

```bash
factgraph repo-commit ./repo model.fg --model-key customer-domain --out commit.json
```

Without one, the normalized model ID is used.

`model_key` renames are never guessed. Within a model, explicit `identity "..."` tokens preserve semantic element identity across display-name renames; legacy name-derived elements remain rename-sensitive.

## Commit invariant

Before a revision is installed, Factgraph:

1. parses and normalizes the source;
2. validates it;
3. encodes the semantic-core metamodel population;
4. encodes the compiler-envelope population;
5. decodes both;
6. requires semantic equality for the core;
7. requires full manifest equality for the envelope;
8. writes all artifacts;
9. hashes them;
10. installs the immutable revision and advances the head.

No invalid or non-round-trippable model is committed.

## Metamodel migration

A repository metamodel migration does not change the represented domain model.

For each selected head:

```text
old envelope population
      |
      | old codec
      v
 normalized model
      |
      | new codec
      v
new envelope population
```

A new immutable revision records:

```json
{
  "operation": "metamodel_migration",
  "operation_details": {
    "from_version": "1",
    "to_version": "2"
  }
}
```

The domain diff between the old and new revision is expected to be empty.

## Verification

`repo-verify` is intentionally expensive relative to simply reading indexes.

It checks each revision from its files rather than accepting the index at face value.

Example:

```bash
factgraph repo-verify ./repo --out verification.json
```

The result is a file containing `passed`, error/warning arrays, metamodel checks, and per-revision checks.

## Tamper behavior

If an immutable artifact changes after commit, its hash check fails.

If a metamodel population is structurally valid but semantically tampered with, v2 integrity rows and the repository's model/population equality checks cause verification to fail.

The repository provides hash-based integrity evidence for revision contents. v0.8 additionally supports optional append-only Ed25519 attestations stored outside immutable revision directories. An attestation proves that the holder of a registered private key signed the canonical revision payload; it does not by itself establish organizational trust, authorization, or key-revocation policy. See `REPOSITORY_EVOLUTION.md`.

## History model

v0.7 introduced one linear head per `model_key`. v0.8 retains that format for compatibility and layers repository refs over it.

Current repositories have:

- a default `main` branch;
- named branches;
- immutable tags;
- ordinary one-parent commits;
- two-parent semantic merge commits;
- a verified revision DAG.

A v0.7 repository with no refs file is read as if its historical head were the `main` branch.

Remote synchronization, garbage collection, rebasing/history rewriting, and automatic conflict resolution remain outside the current scope. See `REPOSITORY_EVOLUTION.md` and `V0_8_MILESTONE.md`.

## Collaboration

v0.9 adds filesystem remotes, content-addressed packs, remote-tracking refs, fast-forward-only push, and destination-local trust policy. See `COLLABORATION.md`.
