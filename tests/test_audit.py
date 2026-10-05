from pathlib import Path
import json

from factgraph.audit import build_audit, obligations, write_audit_bundle
from factgraph.normalize import normalize_model
from factgraph.parser import parse_model

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    return normalize_model(parse_model((ROOT / "examples" / name).read_text()))


def test_obligations_are_conceptual_not_target_features():
    model = load("role_semantics.fg")
    obs = obligations(model)
    kinds = {o.kind for o in obs}
    assert "fact_set_semantics" in kinds
    assert "ring" in kinds
    sym = next(o for o in obs if o.kind == "ring")
    assert "symmetric" in sym.reading.lower()
    assert "postgres" not in sym.reading.lower()


def test_audit_separates_claimed_from_live_observed():
    model = load("richer_constraints.fg")
    report = build_audit(model)
    assert report["format"] == "factgraph-semantic-portability-audit-v1"
    assert report["summary"]["obligation_count"] > 0
    pg = [o["verdicts"]["postgres"] for o in report["obligations"]]
    assert any(v["preservation"] == "preserved_claimed" for v in pg)
    assert all(v["evidence_level"] != "live_observed" for v in pg)


def test_gap_probe_is_reported_as_weakening_not_success():
    model = load("role_semantics.fg")
    fake_live = {
        "postgres": {
            "target": "postgres",
            "status": "completed",
            "passed": True,
            "results": [
                {
                    "case_id": "pg-gap-ring-constraint:ring:symmetric:Knows",
                    "mode": "runtime",
                    "passed": True,
                }
            ],
        }
    }
    report = build_audit(model, targets=["postgres"], live_reports=fake_live)
    sym = next(o for o in report["obligations"] if o["kind"] == "ring")
    assert sym["verdicts"]["postgres"]["preservation"] == "weakened_observed"
    assert sym["verdicts"]["postgres"]["evidence_level"] == "live_observed"


def test_audit_bundle_writes_file_handoff(tmp_path):
    model = load("warehouse.fg")
    out = tmp_path / "audit"
    report = write_audit_bundle(model, out)
    for rel in [
        "audit.json",
        "audit.md",
        "obligations.json",
        "transformation_trace.json",
        "witnesses/index.json",
        "AUDIT_SUMMARY.json",
    ]:
        assert (out / rel).is_file(), rel
    idx = json.loads((out / "witnesses/index.json").read_text())
    assert len(idx) == report["summary"]["obligation_count"]
    assert all((out / row["file"]).is_file() for row in idx)


def test_audit_cli_writes_target_scoped_files_without_services(tmp_path):
    from factgraph.cli import main
    out = tmp_path / "audit-cli"
    rc = main(["audit", str(ROOT / "examples" / "role_semantics.fg"), "--out-dir", str(out)])
    assert rc == 0
    assert (out / "audit.md").is_file()
    for target in ["postgres", "mongo"]:
        assert (out / "targets" / target / "verdicts.json").is_file()
        live = json.loads((out / "targets" / target / "live.json").read_text())
        assert live["status"] == "not_run"


def _completed_shared_report(model, target: str):
    from factgraph.shared_witness_execution import build_cases, evidence_identity, _evaluate

    cases = build_cases(model, target)
    rows = []
    for case in cases:
        if case.lowering_status != "lowered":
            rows.append(_evaluate(case, None))
        elif case.expected_write_outcome == "prevented":
            rows.append(_evaluate(case, "prevented"))
        elif case.expected_write_outcome == "realized":
            rows.append(_evaluate(
                case,
                "realized",
                poststate_passed=(True if case.poststate_required_for_semantic_proof else None),
                poststate_results=([{"passed": True}] if case.poststate_required_for_semantic_proof else None),
            ))
        else:
            rows.append(_evaluate(case, "realized"))
    asserted = [r for r in rows if r.get("passed") is not None]
    return {
        "format": "factgraph-shared-witness-live-v2",
        "target": target,
        "status": "completed",
        "model": model.name,
        **evidence_identity(model, target, cases),
        "case_count": len(rows),
        "asserted_case_count": len(asserted),
        "poststate_pending_count": 0,
        "passed": all(r["passed"] for r in asserted),
        "results": rows,
    }


def test_shared_semantic_witness_is_authoritative_live_verdict_channel():
    model = load("richer_constraints.fg")
    shared = _completed_shared_report(model, "postgres")
    report = build_audit(model, targets=["postgres"], shared_live_reports={"postgres": shared})
    observed = [o["verdicts"]["postgres"] for o in report["obligations"] if o["verdicts"]["postgres"]["evidence_source"] == "shared_semantic_witness"]
    assert observed
    assert all(v["evidence_level"] == "live_observed" for v in observed)
    assert all(v["preservation"] in {"preserved_observed", "weakened_observed", "claim_falsified"} for v in observed)
    assert report["summary"]["targets"]["postgres"]["shared_semantic_live_observed"] == len(observed)


def test_pending_shared_poststate_does_not_promote_primary_verdict():
    from factgraph.shared_witness_execution import build_cases, evidence_identity, _evaluate

    model = normalize_model(parse_model((ROOT / "examples" / "portability" / "total_participation.fg").read_text()))
    cases = build_cases(model, "postgres")
    target_case = next(c for c in cases if c.poststate_required_for_semantic_proof)
    pending = _evaluate(target_case, "realized")
    rows = [_evaluate(c, None) if c is not target_case else pending for c in cases]
    shared = {
        "format": "factgraph-shared-witness-live-v2",
        "target": "postgres",
        "status": "completed",
        "model": model.name,
        **evidence_identity(model, "postgres", cases),
        "case_count": len(rows),
        "asserted_case_count": 0,
        "poststate_pending_count": 1,
        "passed": False,
        "results": rows,
    }
    report = build_audit(model, targets=["postgres"], shared_live_reports={"postgres": shared})
    obligation = next(o for o in report["obligations"] if o["id"] == target_case.obligation_id)
    verdict = obligation["verdicts"]["postgres"]
    assert verdict["preservation"] == "weakened"
    assert verdict["evidence_source"] != "shared_semantic_witness"
    assert verdict["semantic_live_execution"]["result"]["passed"] is None
