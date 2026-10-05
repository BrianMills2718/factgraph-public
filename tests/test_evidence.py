from __future__ import annotations

import json
from pathlib import Path

import pytest

from factgraph.cli import load, main
from factgraph.evidence import EvidenceValidationError, load_shared_live_report
from factgraph.shared_witness_execution import build_cases, evidence_identity, _evaluate

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "examples" / "portability"


def completed_report(model, target: str) -> dict:
    cases = build_cases(model, target)
    results = []
    for case in cases:
        if case.lowering_status != "lowered":
            results.append(_evaluate(case, None))
        elif case.expected_write_outcome == "prevented":
            results.append(_evaluate(case, "prevented"))
        elif case.expected_write_outcome == "realized":
            results.append(_evaluate(
                case,
                "realized",
                poststate_passed=True if case.poststate_required_for_semantic_proof else None,
                poststate_results=[{"passed": True}] if case.poststate_required_for_semantic_proof else None,
            ))
        else:
            results.append(_evaluate(case, "realized"))
    asserted = [row for row in results if row.get("passed") is not None]
    return {
        "format": "factgraph-shared-witness-live-v2",
        "target": target,
        "status": "completed",
        "model": model.name,
        **evidence_identity(model, target, cases),
        "case_count": len(results),
        "asserted_case_count": len(asserted),
        "poststate_pending_count": 0,
        "passed": all(row["passed"] for row in asserted),
        "results": results,
    }


def test_imported_shared_live_evidence_requires_exact_model_and_plan_fingerprints(tmp_path):
    model = load(PORT / "value_range.fg")
    report = completed_report(model, "postgres")
    path = tmp_path / "postgres.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    loaded = load_shared_live_report(path, model, "postgres")
    assert loaded["case_plan_sha256"] == report["case_plan_sha256"]

    bad = dict(report)
    bad["case_plan_sha256"] = "0" * 64
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(EvidenceValidationError, match="case_plan_sha256 mismatch"):
        load_shared_live_report(path, model, "postgres")


def test_audit_cli_can_apply_external_shared_live_evidence_without_database_connection(tmp_path):
    model_path = PORT / "value_range.fg"
    model = load(model_path)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "postgres.json").write_text(json.dumps(completed_report(model, "postgres")), encoding="utf-8")

    out = tmp_path / "audit"
    rc = main([
        "audit", str(model_path), "--targets", "postgres", "--out-dir", str(out),
        "--shared-live-dir", str(evidence_dir),
    ])
    assert rc == 0
    imported = json.loads((out / "evidence_import.json").read_text())
    assert imported["validated_target_count"] == 1
    audit = json.loads((out / "audit.json").read_text())
    assert audit["summary"]["targets"]["postgres"]["shared_semantic_live_observed"] > 0
    assert (out / "targets" / "postgres" / "semantic_live.json").is_file()
