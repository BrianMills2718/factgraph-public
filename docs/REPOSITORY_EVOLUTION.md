# Repository evolution: stable identities, refs, semantic merge, and attestations

## Why names cannot be identities

Before v0.8, Factgraph IDs were deterministic functions of names. This was useful for reproducibility but not for history: renaming `User` to `Person` necessarily changed the ID, so a reverse/migration tool could not distinguish a rename from deletion plus creation without a hint.

v0.8 adds explicit semantic identity tokens. The display name remains editable; the identity token expresses continuity.

Every build and repository revision writes `identity_coverage.json`. It distinguishes:

- `stable_across_rename: true` — explicit `identity` token;
- legacy name-derived identity — deterministic but rename-sensitive.

## Branches and tags

Each repository model now has `refs.json`:

```json
{
  "default_branch": "main",
  "branches": {
    "main": "rev-...",
    "feature/person": "rev-..."
  },
  "tags": {
    "v1": "rev-..."
  }
}
```

Branches are mutable pointers. Tags are immutable pointers. Revisions remain immutable.

Useful commands:

```bash
factgraph repo-refs REPO MODEL --out refs.json
factgraph repo-branch REPO MODEL feature/person --from-ref main --out branch.json
factgraph repo-tag REPO MODEL v1 --ref main --out tag.json
factgraph repo-commit REPO model.fg --model-key MODEL --branch feature/person --out commit.json
```

## Semantic merge

A merge uses the revision DAG to locate a common ancestor and then performs a three-way merge over semantic IDs.

```bash
factgraph repo-merge REPO MODEL \
  --ours main \
  --theirs feature/person \
  --out-dir artifacts/merge
```

A clean result writes `merge.json`, `merge.explanation.md`, `merged.fg`, and `merged.manifest.json`. Conflict explanations use ORM/fact-model terminology and describe semantic impact without choosing a resolution.

A conflict returns exit status `2` and writes deterministic conflict IDs. Resolutions are ordinary JSON files:

```json
{
  "conflicts": {
    "merge:object_type:...": "ours"
  }
}
```

Then:

```bash
factgraph repo-merge REPO MODEL \
  --ours main \
  --theirs feature/person \
  --resolutions resolutions.json \
  --out-dir artifacts/resolved
```

Add `--commit` only after the semantic merge is conflict-free. The resulting revision has both branch heads as parents.

## Provenance

`repo-commit` and merge commits may carry caller-supplied `author` and `message` fields. No wall-clock timestamp is invented, preserving deterministic revision identities.

```bash
factgraph repo-commit REPO model.fg \
  --model-key MODEL --branch main \
  --author "Ada" --message "add display name" \
  --out commit.json
```

Provenance is evidence supplied by the caller; it is not authenticated unless the revision is also attested.

## Ed25519 attestations and collaboration

Install the optional signing dependency:

```bash
python -m pip install -e '.[signing]'
```

Generate a key pair:

```bash
factgraph repo-keygen \
  --private-key private.pem \
  --public-key public.pem \
  --out key.json
```

New signatures use the portable v2 revision payload introduced in v0.9, so an attestation can travel with immutable revision content between repositories. Legacy v1 repository-bound attestations remain verifiable. Cryptographic validity is distinct from destination-local trust; see `COLLABORATION.md`.

Sign an immutable revision/ref:

```bash
factgraph repo-sign REPO MODEL \
  --ref main \
  --private-key private.pem \
  --signer "Example signer" \
  --out signature.json
```

Verify all attestations:

```bash
factgraph repo-verify-signatures REPO --out signatures.json
factgraph repo-verify REPO --out repository.json
```

The repository stores registered public keys and append-only attestation JSON separately from revision directories. Adding an attestation therefore does not rewrite the object it authenticates.

## Legacy compatibility

A v0.7 repository with no `refs.json` is interpreted as one synthetic `main` branch pointing at its old head. The next write materializes refs. Legacy models remain valid; stable IDs are opt-in because silently inventing cross-version identity would be the same heuristic the project has consistently avoided.
