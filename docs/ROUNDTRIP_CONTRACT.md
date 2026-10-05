# Round-trip contract

The phrase “round trip” is deliberately qualified in this project.

## Source normalization round trip

The canonical source invariant is:

```text
normalize(parse(print(normalize(parse(source)))))
==
normalize(parse(source))
```

under semantic equality.

## Pure target structural recovery

The PostgreSQL and MongoDB adapters emit structural recovery reports **without consulting semantic sidecars**. These reports say what can be read from the canonical target artifact and list what cannot be reliably reconstructed.

They do not invent conceptual intent that the target artifact does not contain.

## Metadata-preserving semantic round trip

Every target build also emits a namespaced semantic sidecar (`semantic.json`). Reading our own target plus that sidecar can exactly recover the normalized semantic model.

This is not evidence that the target natively preserves that information. It is explicit compiler-carried recovery metadata.

## Arbitrary database import

Not implemented and not claimed. A random PostgreSQL table with three foreign keys does not uniquely determine whether the author intended a ternary fact, an entity with three references, an objectified fact, denormalized storage, or an implementation artifact.

## v0.3 evidence file

Every build writes `roundtrip.json`. It independently records:

- canonical source semantic equality;
- pure PostgreSQL emitter/structural-reader consistency;
- pure Mongo target-spec/structural-reader consistency;
- sidecar-assisted exact recovery;
- cross-target equality of the sidecar-recovered normalized models.

The file explicitly records `native_exact_semantic_roundtrip_claimed: false` for target structure.
