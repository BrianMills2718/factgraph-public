# v0.8 milestone — repository evolution

v0.8 turns the v0.7 linear content-verified repository into a local semantic revision DAG.

## Contract

The milestone is complete when all of the following are true:

1. source declarations may carry semantic identities that are independent of display names;
2. semantic diff aligns equal stable IDs before names or migration hints;
3. repository models have a default `main` branch plus named branches and immutable tags;
4. revisions may have more than one parent and repository verification checks a DAG, not a linear chain;
5. three-way merge operates on normalized semantic elements, not source text;
6. unresolved delete/modify or modify/modify changes are emitted as deterministic conflict records;
7. a file-carried resolution map may choose `ours`, `theirs`, `base`, or `delete` per conflict;
8. clean merges may be committed as two-parent revisions;
9. revisions may carry deterministic provenance fields supplied by the caller;
10. optional Ed25519 attestations sign immutable revision content without mutating the revision directory;
11. repository verification validates registered attestations as well as revision hashes/graph integrity.

## Stable identity syntax

```text
model TeamApp identity "team-app" {
  value UserId: UUID identity "user-id"

  entity User identity "user" {
    id userId: UserId identity "user.id"
  }

  fact Membership identity "membership"(
    member: User identity "membership.member",
    team: Team identity "membership.team"
  ) {
    unique(member, team)
  }
}
```

The identity token is normalized into a separate `uid:*` namespace. Renaming `User` to `Person` while retaining `identity "user"` is therefore a rename of one semantic element, not a drop+add pair.

Declarations without `identity` retain the v0.1-v0.7 deterministic name-derived IDs. They remain valid, but a rename may require an explicit migration hint because the compiler has no basis for claiming continuity.

## Merge scope

v0.8 merge is deliberately local and semantic:

```text
           base
          /    \
       ours   theirs
          \    /
        semantic merge
             |
       merged model or
     explicit conflicts
```

The merge engine currently compares object types, facts, roles, readings, constraints, field projections, samples, analyses, and model name by semantic identity. It does not attempt natural-language conflict resolution or heuristic rename detection.

## Signing scope

Signatures are append-only attestations stored outside immutable revision directories. The signed payload commits to repository identity, revision identity, parents, metamodel version, semantic/manifest hashes, provenance, operation metadata, and artifact hashes.

`cryptography` is an optional dependency exposed through the `signing` extra. Repository integrity verification remains usable without signing support when no attestations exist.

## Explicit non-claims

v0.8 does not provide:

- remote repositories;
- network synchronization;
- distributed locking or consensus;
- trust policy / certificate chains;
- key revocation infrastructure;
- Git-compatible objects/protocols;
- automatic semantic conflict resolution;
- garbage collection of unreachable revisions;
- universal rename inference for legacy name-derived IDs.
