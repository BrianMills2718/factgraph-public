import json
from pathlib import Path

from factgraph.audit import build_audit
from factgraph.cli import load
from factgraph.conformance import bundle
from factgraph.witness import synthesize_counterexample


ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "portability"


def _matching(report, assertion):
    matches = [o for o in report["obligations"] if o["kind"] == assertion["kind"]]
    needle = assertion.get("reading_contains")
    if needle:
        matches = [o for o in matches if needle in o["reading"]]
    return matches


def test_portability_benchmark_expectations_are_executable_and_current():
    spec = json.loads((BENCH / "expectations.json").read_text(encoding="utf-8"))
    assert len(spec["cases"]) >= 20
    for filename, assertions in spec["cases"].items():
        model = load(BENCH / filename)
        report = build_audit(model, targets=spec["targets"])
        for assertion in assertions:
            matches = _matching(report, assertion)
            assert len(matches) == 1, (filename, assertion, [m["reading"] for m in matches])
            actual = {target: matches[0]["verdicts"][target]["preservation"] for target in spec["targets"]}
            assert actual == assertion["expected"], (filename, assertion, actual)


def test_portability_benchmark_native_claims_have_structural_conformance_coverage():
    for path in sorted(BENCH.glob("*.fg")):
        model = load(path)
        evidence = bundle(model)
        assert evidence["coverage"]["complete"], path.name
        assert evidence["static"]["all_passed"], path.name


def test_portability_benchmark_has_isolated_locally_irreducible_source_counterexamples():
    for path in sorted(BENCH.glob("*.fg")):
        model = load(path)
        report = build_audit(model)
        for obligation in report["obligations"]:
            result = synthesize_counterexample(model, obligation["id"], obligation["kind"])
            assert result.status == "isolated", (path.name, obligation["id"], result.note)
            assert result.target_violation_observed, (path.name, obligation["id"])
            assert result.locally_irreducible, (path.name, obligation["id"])
            assert all(v.obligation_id == obligation["id"] for v in result.violations)
