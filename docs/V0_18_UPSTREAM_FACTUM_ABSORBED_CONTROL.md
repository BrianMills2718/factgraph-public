# v0.18 — provenance-locked upstream Factum control and absorbed relational mappings

## Purpose

v0.18 moves the Factum experiment from “a Factgraph-authored source passed through Factum” toward a stronger independent control: an **upstream-authored Factum model** is fetched from an immutable public commit, processed by an **exact pinned Factum generator commit**, and then audited by Factgraph without altering the generated PostgreSQL DDL.

The milestone also fixes a real auditor limitation exposed by that independent mapper: functional ORM facts are commonly **absorbed into an entity table** instead of receiving one table per fact. Factgraph's external PostgreSQL mapping v1/v2 could not represent that layout. Mapping v3 adds a deliberately narrow explicit absorption contract rather than guessing arbitrary denormalized schemas.

## Pinned upstream objects

Generator:

- project: Factum ORM 0.5.0
- repository: `Volland/factum-orm`
- commit: `7897dd0c4303b9eea46c60342f1632b27a624744`
- bundled CLI: `bin/factum.js`
- bundled CLI Git blob: `b6fa78285c3635e8f8119bd6fbf6bf5db3dd63e6`
- package.json Git blob: `2079186fbc29b4d1214f3acb732bc0ccb99355cf`
- package-lock.json Git blob: `c6543f0df885f36880c5f7d7904addb090b95882`

Source model:

- project: `Volland/factum-book-models`
- commit: `e5990315e9a2a7905d129c5626cbde09dab2a842`
- repository tree: `0436823ffda532782557cfc049fa834c04da8870`
- path: `models/fig-mandatory.orm.json`
- Git blob: `dab575088c3642a26d4b6850095726a86d48ed0e`
- source SHA-256: `f5111e58c806674b262d582d9bc4ddd7dd5b41c9a0b33a24b37d814a6bace807`

The upstream source itself is fetched from the pinned checkout at execution time rather than vendored into the Factgraph source release.

## Why this is a control, not a cherry-picked failure

The upstream model contains a binary `Person works for Company` fact with:

- uniqueness on the Person role;
- mandatory participation on the Person role.

Factum's Rmap treats a functional binary as an absorbed column on the uniquely constrained role player's table. For this case, the expected physical representation is a `Person` row with its identifier plus a `companyName` reference. Mandatory participation should make that absorbed column non-null.

So the pre-live hypothesis is **preservation on the tested cases**, not weakening. Factgraph will not count that hypothesis as evidence until real PostgreSQL executes:

1. a source-invalid `Person` with no `works` occurrence; and
2. a source-valid `Person` + `Company` + `works` occurrence.

The invalid case should be rejected and the valid case accepted before the obligation is refined to `preserved_on_tested_cases`.

The existing Factgraph-authored m:n `PersonHasSkill` case remains a separate stress case whose pre-live hypothesis is weakening. Keeping both cases is intentional experimental balance.

## External PostgreSQL mapping v3

A fact mapping may now state:

```json
{
  "storage_mode": "absorbed",
  "anchor_role_id": "<role-id>",
  "table": "Person",
  "role_columns": {
    "<person-role>": ["personNr"],
    "<company-role>": ["companyName"]
  }
}
```

This is accepted only when:

- the mapping format is v3;
- the fact is binary and non-objectified;
- it has no relationship-owned fields;
- the anchor role is played by an entity type;
- the target table is exactly the mapped anchor-entity table;
- the anchor role columns equal that entity's mapped identifier columns;
- different absorbed facts do not ambiguously overlap non-anchor columns.

Factgraph still does **not** infer this structure from SQL names.

## Population lowering semantics

For a source-valid population:

```text
Company(C)
Person(P)
works(P, C)
```

the canonical separate fact-table operations are coalesced into the explicit absorbed physical layout:

```sql
INSERT INTO "Company" ("companyName") VALUES (...);
INSERT INTO "Person" ("personNr", "companyName") VALUES (..., ...);
```

For the mandatory counterexample:

```text
Person(P)
(no works occurrence)
```

the physical operation remains:

```sql
INSERT INTO "Person" ("personNr") VALUES (...);
```

The third-party schema decides whether that is legal.

Repeated occurrences of the same absorbed fact are not silently collapsed. Factgraph emits a second row carrying the same anchor key so scalar/PK target semantics get to reject or admit the source multiplicity.

## Mandatory post-state semantics

If an absorbed relationship write is accepted, relationship presence is tested by the non-anchor mapped columns being non-null. For the `works` example, a source query equivalent to “does this Person participate in works?” becomes a count over `Person` with the matching `personNr` and `companyName IS NOT NULL`.

## Factum reference-mode transport typing

The upstream model uses `refMode: "nr"` without an explicit data type. Factum 0.5.0's Rmap maps reference-mode suffixes such as `nr`, `no`, `number`, `id`, `count`, and `seq` to integer. Factgraph now mirrors that documented convention during Factum import.

This matters for audit validity: a string key inserted into an integer target could otherwise be rejected for a transport/type reason and falsely look like the semantic mandatory rule was preserved.

## Evidence boundary

This packaging sandbox still has no PostgreSQL service or pinned upstream checkouts. The local runner therefore records `upstream_checkouts_unavailable`. Hosted CI is configured to check out the exact commits, verify provenance, run the bundled Factum CLI directly with Node, and require live PostgreSQL.

No code inspection, DDL inspection, or prepared witness is counted as an observed preservation result.
