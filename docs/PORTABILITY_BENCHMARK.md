# Semantic portability benchmark

`examples/portability/` is a public, deliberately small benchmark for constraint-level semantic fidelity across PostgreSQL, MongoDB, and TypeDB.

The benchmark is not a performance workload and does not try to resemble a large production application. Each model isolates a semantic pattern so a reviewer can understand why target behavior differs.

## v0.10 corpus

The release contains 20 model files covering:

- simple identifier;
- compound identifier;
- unary fact set semantics;
- n-ary fact set semantics;
- binary single-role uniqueness;
- partial uniqueness in a ternary fact;
- total participation;
- frequency `0..1`;
- frequency `0..2`;
- frequency `1..2` without reinterpreting it as total participation;
- integer range;
- string enumeration;
- Decimal range target-literal gap;
- Date enumeration target-literal gap;
- constrained ValueType role;
- subtyping;
- logical symmetry;
- unordered same-player roles;
- subset/equality/exclusion projections;
- objectification plus relationship-owned constrained data.

`expectations.json` records the expected structural/declarative semantic direction for each focal obligation. A live run may strengthen `preserved_claimed` to `preserved_observed` or `weakened` to `weakened_observed`; it must not silently reverse the semantic direction.

## Build

Static/structural evidence:

```bash
python scripts/build_portability_benchmark.py \
  --out-dir artifacts/portability_benchmark
```

With live target environment variables supplied, the same script executes each model's target conformance suite and includes those observations in each audit.

The generated bundle includes target artifacts, capability reports, obligation-level audit files, conformance cases/results, the expectation matrix, and mutation catalog.

## What this benchmark does not prove

Passing finite examples is not a proof of arbitrary semantic equivalence. The benchmark is an executable regression corpus and comparison instrument. It should grow based on real transformation failures, not simply to increase the case count.
