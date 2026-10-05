# v0.7 milestone — versioned metamodel evolution and model repository

## Goal

v0.7 turns the v0.6 metamodel-as-data result into operational infrastructure.

The milestone has two linked capabilities:

1. **version the Factgraph metamodel itself** and migrate stored model populations between compatible metamodel versions; and
2. **store immutable model revisions in a content-verified repository** that records which metamodel version encoded each revision.

The narrow invariant is:

```text
stored population under metamodel A
        |
        | decode with A
        v
 represented normalized model
        |
        | encode with B
        v
stored population under metamodel B
```

A metamodel migration passes only if the represented model survives the declared equality contract.

This is deliberately not a row-rewriting heuristic.

---

## Versioned metamodel registry

The package now ships:

```text
src/factgraph/data/metamodel/v1.fg
src/factgraph/data/metamodel/v2.fg
```

`v1` is the metamodel contract shipped by factgraph 0.6.

`v2` adds three encoding-envelope/integrity facts:

```text
MM_ModelCodecVersion
MM_ModelSemanticHash
MM_ModelManifestHash
```

Those facts do not add domain semantics to the represented user model. They make a stored population more self-describing and tamper-evident.

The semantic diff between v1 and v2 is generated with the same ordinary Factgraph semantic diff engine used for user models.

### CLI

```bash
factgraph metamodel-versions --out artifacts/metamodel_versions/versions.json

factgraph metamodel-diff 1 2 \
  --out-dir artifacts/metamodel_versions/v1_to_v2_diff
```

---

## Population migration contract

A population migration uses the codec of the source metamodel version to recover the represented model, then uses the destination codec to encode that model again.

```bash
factgraph metamodel-migrate old.population.json \
  --to-version 2 \
  --out-dir artifacts/population_migration
```

Envelope mode is the default and requires complete compiler-manifest equality.

`--core` weakens the contract intentionally to semantic equality only.

Generated files include:

```text
source.population.json
migrated.population.json
recovered.normalized.fg
recovered.manifest.json
migration.json
```

A future metamodel version that cannot recover the source model must fail the migration rather than guess.

---

## Model repository

v0.7 introduces a deterministic filesystem repository.

A repository stores:

- repository configuration;
- immutable snapshots of every supported metamodel version;
- one or more logical model histories;
- immutable model revisions;
- original and canonical source;
- semantic and compiler manifests;
- validation evidence;
- semantic-core and compiler-envelope metamodel populations;
- SHA-256 hashes for every revision artifact;
- parent revision links;
- the metamodel version used by each revision.

The repository is intentionally not a network service and has no database dependency.

### Initialize

```bash
factgraph repo-init ./models.repo \
  --name MyModels \
  --metamodel-version 1 \
  --out artifacts/repo-init.json
```

### Commit

```bash
factgraph repo-commit ./models.repo examples/warehouse.fg \
  --model-key warehouse \
  --out artifacts/warehouse-commit.json
```

A commit validates and round-trips the model through the selected metamodel before the immutable revision is installed.

### List and log

```bash
factgraph repo-list ./models.repo --out artifacts/models.json
factgraph repo-log ./models.repo warehouse --out artifacts/warehouse.log.json
```

### Verify

```bash
factgraph repo-verify ./models.repo --out artifacts/repository-verification.json
```

Verification rechecks:

- metamodel snapshots;
- repository/model indexes;
- linear parent history;
- revision artifact hashes;
- semantic and manifest hashes;
- canonical normalized source;
- metamodel population decoding;
- canonical population re-encoding.

A hash mismatch or population mismatch fails verification.

### Semantic diff between revisions

```bash
factgraph repo-diff ./models.repo warehouse REV_A REV_B \
  --out-dir artifacts/warehouse-diff
```

This compares the represented normalized models, not repository metadata.

Therefore a metamodel-only re-encoding revision has an empty domain semantic diff.

### Migrate repository heads to a new metamodel

```bash
factgraph repo-migrate-metamodel ./models.repo \
  --to-version 2 \
  --out artifacts/repository-migration.json
```

The operation is immutable: it creates a new revision whose parent is the old head. The represented domain model is unchanged; the storage population/metamodel version changes.

---

## Repository revision identity

Revision IDs are deterministic digests over:

- logical `model_key`;
- parent revision;
- target metamodel version;
- full model manifest hash;
- operation type and operation details.

Exact recommits of the current content/metamodel are idempotent rather than creating noise revisions.

The v0.7 repository is deliberately **linear-history only**. Branch/merge semantics are future work.

---

## Evidence fixture

`scripts/build_repository_demo.sh` creates a complete repository handoff under:

```text
artifacts/repository_demo/
```

The demo:

1. initializes a repository under metamodel v1;
2. commits two `TeamApp` domain revisions;
3. semantic-diffs those revisions;
4. migrates the repository head from metamodel v1 to v2;
5. verifies every revision and hash;
6. checks out the resulting head.

Separate artifacts under `artifacts/metamodel_versions/` demonstrate a standalone v1→v2 population migration.

---

## Non-claims

v0.7 does **not** claim:

- universal metamodel compatibility;
- that arbitrary future metamodel changes will be migratable;
- Git-compatible branching/merging;
- distributed repository synchronization;
- arbitrary database import;
- model-instance data migration between domain model revisions;
- Python compiler bootstrapping.

The repository is a deterministic semantic artifact store, not a source-control replacement.
