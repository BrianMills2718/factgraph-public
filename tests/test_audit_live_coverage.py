"""Legacy live evidence must cover every matching runtime case before an observed verdict.

ChatGPT round 2 (chat 6abb9bee): a "completed" live report that covered only
some of an obligation's runtime cases still promoted the obligation to
preserved_observed / weakened_observed, because every result it *did* contain
passed.  The audit must fail closed and record why.

Bundled generators currently emit at most one runtime case per obligation and
target, so these tests add a second runtime case per obligation to exercise
the multi-case path the verdict logic must handle.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from factgraph import audit
from factgraph.audit import build_audit
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = {"preserved_observed", "weakened_observed"}


@pytest.fixture
def two_case_audit(monkeypatch):
    original = audit._cases

    def doubled(model):
        out = original(model)
        return {
            t: [*cases, *(dataclasses.replace(c, id=c.id + "#second") for c in cases if c.mode == "runtime")]
            for t, cases in out.items()
        }

    monkeypatch.setattr(audit, "_cases", doubled)
    model = normalize_model(parse_model((ROOT / "examples" / "richer_constraints.fg").read_text()))
    base = build_audit(model, targets=["postgres"])
    runtime_ids = sorted(
        c["case_id"]
        for ob in base["obligations"]
        for c in ob["verdicts"]["postgres"]["cases"]
        if c["mode"] == "runtime"
    )
    assert runtime_ids and any(i.endswith("#second") for i in runtime_ids)
    return model, runtime_ids


def _report(results, **extra):
    return {"postgres": {"target": "postgres", "status": "completed", "results": results, **extra}}


def _verdicts(model, live):
    report = build_audit(model, targets=["postgres"], live_reports=live)
    return [
        v
        for ob in report["obligations"]
        for v in [ob["verdicts"]["postgres"]]
        if any(c["mode"] == "runtime" for c in v["cases"])
    ]


def test_complete_report_is_observed(two_case_audit):
    model, ids = two_case_audit
    vs = _verdicts(model, _report([{"case_id": i, "mode": "runtime", "passed": True} for i in ids]))
    observable = [v for v in vs if v["status"] in {"native_enforced", "emulated_enforced", "represented_not_enforced"}]
    assert observable and all(v["preservation"] in OBSERVED for v in observable)
    assert all(v["live_coverage"]["complete"] for v in vs)


def test_partial_report_fails_closed_and_records_reason(two_case_audit):
    model, ids = two_case_audit
    first_only = [i for i in ids if not i.endswith("#second")]
    vs = _verdicts(model, _report([{"case_id": i, "mode": "runtime", "passed": True} for i in first_only]))
    assert vs
    for v in vs:
        assert v["preservation"] not in OBSERVED
        assert v["evidence_level"] != "live_observed"
        cov = v["live_coverage"]
        assert cov["complete"] is False
        assert cov["observed_runtime_cases"] == 1 and cov["runtime_cases"] == 2
        assert len(cov["missing_case_ids"]) == 1 and cov["missing_case_ids"][0].endswith("#second")
        assert "not promoted" in cov["reason"]


def test_partial_report_with_failure_still_falsifies(two_case_audit):
    model, ids = two_case_audit
    first_only = [i for i in ids if not i.endswith("#second")]
    vs = _verdicts(model, _report([{"case_id": i, "mode": "runtime", "passed": False} for i in first_only]))
    enforced = [v for v in vs if v["status"] in {"native_enforced", "emulated_enforced"}]
    assert enforced and all(v["preservation"] == "claim_falsified" for v in enforced)


def test_legacy_runner_shape_partial_is_not_observed(two_case_audit):
    """Shape emitted by factgraph.live.postgres.run: structural rows + runtime rows with steps."""
    model, ids = two_case_audit
    first_only = [i for i in ids if not i.endswith("#second")]
    results = [
        {"case_id": "pg-structural-anything", "mode": "structural", "passed": True, "expected": "pass", "actual": "pass", "evidence": None},
        *(
            {"case_id": i, "feature": "x", "mode": "runtime", "passed": True, "description": "d", "steps": []}
            for i in first_only
        ),
    ]
    live = _report(results, server="PostgreSQL 16", model=model.name, passed=True, case_count=len(results))
    vs = _verdicts(model, live)
    assert vs and all(v["preservation"] not in OBSERVED for v in vs)
    # Same shape, complete coverage: observed.
    results += [{"case_id": i, "feature": "x", "mode": "runtime", "passed": True, "description": "d", "steps": []} for i in ids if i.endswith("#second")]
    vs = _verdicts(model, _report(results, passed=True))
    assert any(v["preservation"] in OBSERVED for v in vs)


def test_extra_unmatched_result_does_not_fill_coverage(two_case_audit):
    model, ids = two_case_audit
    first_only = [i for i in ids if not i.endswith("#second")]
    results = [{"case_id": i, "mode": "runtime", "passed": True} for i in first_only]
    results.append({"case_id": "pg-case-that-matches-nothing", "mode": "runtime", "passed": True})
    vs = _verdicts(model, _report(results))
    assert vs and all(v["preservation"] not in OBSERVED for v in vs)
    assert all(v["live_coverage"]["complete"] is False for v in vs)


def test_verdict_without_live_results_has_no_coverage_block():
    """Committed artifacts (no live runs) keep their exact shape."""
    model = normalize_model(parse_model((ROOT / "examples" / "richer_constraints.fg").read_text()))
    report = build_audit(model, targets=["postgres"])
    assert all("live_coverage" not in ob["verdicts"]["postgres"] for ob in report["obligations"])
