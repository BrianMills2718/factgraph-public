# Semantic collaboration

Factgraph collaboration operates on immutable semantic repository revisions rather than source-text patches.

## Configure a filesystem remote

Initialize two repositories, then configure one as a remote:

```bash
factgraph repo-remote ./work origin ../hub --out remote.json
factgraph repo-remotes ./work --out remotes.json
```

The stored path is relative to the local repository when possible. The remote repository identity is pinned; if the path later points to a different repository ID, Factgraph refuses to use it.

## Push

```bash
factgraph repo-push ./work origin team-app \
  --branch main \
  --out-dir artifacts/push
```

Artifacts include:

```text
artifacts/push/
  transfer.json
  pack/
    pack.json
    objects/sha256/...
```

A push is fast-forward-only. Immutable objects are verified/installed before branch movement. If the destination branch is protected, the candidate revision must also satisfy the destination's trust policy.

## Fetch

```bash
factgraph repo-fetch ./work origin team-app \
  --branch main \
  --out-dir artifacts/fetch
```

Fetch imports immutable revisions and updates a remote-tracking ref such as:

```text
origin/main -> rev-...
```

It does **not** advance `main`. Merge is always explicit.

## Trust a signing key

Generate a keypair outside the repository, distribute only the public key, then explicitly trust it on the repository whose policy should rely on it:

```bash
factgraph repo-trust-key ./hub release.pub.pem \
  --label "release key" \
  --out trust-key.json
```

Protect a branch:

```bash
factgraph repo-protect-branch ./hub team-app main \
  --min-signatures 1 \
  --key-id ed25519:... \
  --out protect-main.json
```

Inspect/evaluate policy:

```bash
factgraph repo-trust-policy ./hub --out trust-policy.json
factgraph repo-trust-evaluate ./hub team-app \
  --ref main --branch main \
  --out trust-evaluation.json
```

Revoke a key:

```bash
factgraph repo-revoke-key ./hub ed25519:... \
  --reason "rotated" \
  --out revoked.json
```

Revocation is a local policy decision. It does not delete transferred public keys or attestations, and can cause repository verification to reject an existing protected branch head that was formerly acceptable.

## Signature portability

New attestations use v2 signing payloads. They are portable because repository identity is not signed. The immutable revision itself—model key, revision ID, semantic/manifest hashes, parents, operation/provenance, and artifact hashes—is signed.

Old v1 attestations remain verifiable in the repository where they were created. They are deliberately not copied by v0.9 transfer packs; `pack.json` records skipped non-portable attestation paths.

## Merge after fetch

Use the remote-tracking revision explicitly (or create a branch pointing to it), then run semantic merge. Conflicts generate `merge.explanation.md` with fact-model terminology explaining why the decision matters.

Factgraph still will not decide that an `Int` value type should beat a conflicting `Bool`, or that deleting a role is preferable to keeping a modified role. Those are conceptual-model decisions.

## Why fetch and push are conservative

A database/schema modeling repository is valuable precisely because semantic changes carry meaning. Factgraph therefore avoids conveniences that obscure intent:

- fetch never merges;
- push never force-updates;
- a valid signature is not automatically a trusted signature;
- imported keys are not automatically trusted;
- merge conflicts are explained but not guessed away.
