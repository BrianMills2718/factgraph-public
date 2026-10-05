# v0.9 milestone — semantic collaboration

## Goal

Turn the v0.8 local semantic-history repository into a small, auditable collaboration layer without claiming a distributed VCS or network service.

v0.9 adds three things together because they constrain one another:

1. **content-addressed repository transfer** between filesystem Factgraph repositories;
2. **destination-local trust policy** for portable revision attestations;
3. **ORM-aware merge explanations** for conflicts that still require a human decision.

The milestone is successful when transfer, trust, and merge-assistance are all represented as files and verified by tests/release evidence.

## Remote transport contract

The only v0.9 transport is another Factgraph repository reachable through the filesystem.

A configured remote records:

- transport = `filesystem`;
- a portable relative path where possible;
- the remote repository ID/name.

`repo-fetch`:

- resolves one remote branch;
- builds a deterministic transfer pack;
- verifies every content-addressed object by SHA-256;
- imports immutable revisions/attestations/public keys;
- writes `remote_tracking[remote][branch]`;
- **does not advance a local branch**.

`repo-push`:

- transfers missing immutable objects through the same pack format;
- refuses non-fast-forward branch movement;
- evaluates the **remote repository's** trust rule for the candidate revision;
- advances the remote branch only when fast-forward and trust checks pass.

Object transfer and branch movement are deliberately separate. A rejected push may leave verified immutable objects present on the remote while leaving the protected branch unchanged.

## Content-addressed pack

A `factgraph-transfer-pack-v1` contains:

```text
pack.json
objects/
  sha256/
    ab/
      <full sha256>
```

`pack.json` maps logical immutable repository paths to object digest/size. It includes the source/destination repository IDs, selected ref/head, reachable revision closure, missing revisions, and pack identity.

The pack permits only:

- immutable revision files;
- append-only revision attestations;
- public-key files needed to verify those attestations.

Mutable repository refs/indexes are reconstructed/updated by the destination implementation, never copied as opaque remote bytes.

## Portable attestations and local trust

v0.8 signatures were repository-bound because the signing payload included `repository_id`. That makes them unsuitable for transferred revisions.

v0.9 introduces:

- `factgraph-revision-attestation-v2`;
- `factgraph-revision-signing-payload-v2`.

The v2 payload signs immutable revision identity/content/ancestry but **not repository identity**, so the attestation can travel with the revision.

Verification remains backward-compatible with v1 repository-bound attestations. Because those v1 signatures are repository-bound, remote packs explicitly skip them and list the skipped paths instead of importing evidence known to become invalid at the destination.

Cryptographic validity is not trust.

A transferred public key is only verification material. It is not trusted until the destination records an explicit policy decision.

`trust-policy.json` can record:

- trusted/revoked key IDs;
- optional human labels/revocation reasons;
- protected model/branch rules;
- minimum accepted trusted signatures;
- optional allow-lists of trusted keys for a protected branch.

Repository verification evaluates protected branch heads and fails when their current revisions no longer satisfy local trust policy.

## Fast-forward only

v0.9 push does not force-update branches.

If remote `main` is not an ancestor of the proposed head, the push transfers immutable objects if needed but refuses to move the remote ref.

The user must fetch and perform an explicit semantic merge/reconciliation first.

## Merge assistance

The v0.8 merge algorithm remains intentionally non-heuristic. v0.9 improves the *explanation*, not the automatic decision making.

Each conflict now includes:

- ORM/fact-model term (`object type`, `fact type`, `role`, `constraint`, etc.);
- semantic impact category;
- changed fields on each side;
- overlapping changed fields;
- why that type of semantic element matters;
- a decision question;
- explicit allowed resolutions.

`repo-merge` writes both:

```text
merge.json
merge.explanation.md
```

The Markdown handoff is intended for a person/coding agent deciding between `ours`, `theirs`, `base`, or `delete`.

No domain-aware resolution is silently selected.

## New CLI surface

```text
repo-remotes
repo-remote
repo-fetch
repo-push
repo-trust-key
repo-revoke-key
repo-protect-branch
repo-trust-policy
repo-trust-evaluate
```

Existing `repo-merge` additionally writes an ORM-aware explanation file.

## Nonclaims

v0.9 does **not** add:

- HTTP/SSH/cloud remote transports;
- background synchronization;
- force push;
- distributed locking or consensus;
- a transparency log;
- organizational identity/PKI;
- automatic merge conflict resolution;
- history rewriting/rebase/cherry-pick;
- repository garbage collection.

The filesystem remote + pack format is intended to make a future network transport possible without making network claims today.
