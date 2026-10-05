#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.audit import build_audit  # noqa: E402
from factgraph.cli import load  # noqa: E402
from factgraph.evidence import EvidenceValidationError, load_shared_live_directory  # noqa: E402
from factgraph.shared_witness_execution import evidence_identity  # noqa: E402

TARGETS = ("postgres", "mongo", "typedb")


def _asserted_map(report: dict) -> dict[tuple[str, str], tuple[str, str, str]]:
    rows: dict[tuple[str, str], tuple[str, str, str]] = {}
    for obligation in report.get("obligations", []):
        oid = obligation["id"]
        for target, verdict in obligation.get("verdicts", {}).items():
            semantic = verdict.get("semantic_live_execution", {}).get("result")
            if semantic and semantic.get("passed") is not None:
                rows[(oid, target)] = (
                    str(verdict.get("preservation")),
                    str(verdict.get("evidence_level")),
                    str(verdict.get("evidence_source")),
                )
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate and offline-replay portable shared-witness live evidence")
    p.add_argument("--root", type=Path, default=ROOT / "artifacts" / "portability_benchmark")
    p.add_argument("--models", type=Path, default=ROOT / "examples" / "portability")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--require-live", action="store_true")
    args = p.parse_args(argv)

    case_rows: list[dict] = []
    failures: list[dict] = []
    asserted_total = 0

    for model_path in sorted(args.models.glob("*.fg")):
        stem = model_path.stem
        case_dir = args.root / "cases" / stem
        if not case_dir.exists():
            continue
        model = load(model_path)
        evidence_dir = case_dir / "shared_witness_execution"
        raw_files: dict[str, dict] = {}
        generated_only: list[str] = []
        invalid_plan: list[dict] = []
        for target in TARGETS:
            file = evidence_dir / f"{target}.json"
            if not file.exists():
                continue
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
            except Exception as exc:
                invalid_plan.append({"target": target, "error": f"invalid JSON: {exc}"}); continue
            raw_files[target] = raw
            if raw.get("format") == "factgraph-shared-witness-execution-plan-v2":
                expected = evidence_identity(model, target)
                mismatched = {k: {"expected": v, "actual": raw.get(k)} for k, v in expected.items() if raw.get(k) != v}
                if mismatched:
                    invalid_plan.append({"target": target, "error": "generated plan fingerprint mismatch", "mismatched": mismatched})
                else:
                    generated_only.append(target)
        if invalid_plan:
            row = {"model": model.name, "status": "invalid_evidence", "errors": invalid_plan}
            case_rows.append(row); failures.append(row); continue
        if generated_only:
            missing_live = sorted(set(TARGETS) - {t for t, raw in raw_files.items() if raw.get("format") == "factgraph-shared-witness-live-v2"})
            row = {
                "model": model.name,
                "status": "not_live" if not args.require_live else "live_incomplete",
                "validated_generated_plan_targets": sorted(generated_only),
                "missing_live_targets": missing_live,
                "asserted_shared_verdict_count": 0,
            }
            case_rows.append(row)
            if args.require_live:
                failures.append(row)
            continue
        try:
            reports, imported = load_shared_live_directory(evidence_dir, model, TARGETS)
        except EvidenceValidationError as exc:
            row = {"model": model.name, "status": "invalid_evidence", "error": str(exc)}
            case_rows.append(row); failures.append(row); continue

        missing = [t for t in TARGETS if t not in reports]
        incomplete = [t for t, r in reports.items() if r.get("status") != "completed"]
        failed_live = [t for t, r in reports.items() if r.get("status") == "completed" and r.get("passed") is False]
        if args.require_live and (missing or incomplete or failed_live):
            row = {
                "model": model.name, "status": "live_incomplete",
                "missing_targets": missing, "incomplete_targets": incomplete, "failed_targets": failed_live,
            }
            case_rows.append(row); failures.append(row); continue

        offline = build_audit(model, targets=TARGETS, shared_live_reports=reports)
        original_path = case_dir / "audit" / "audit.json"
        original = json.loads(original_path.read_text(encoding="utf-8")) if original_path.exists() else None
        replayed = _asserted_map(offline)
        asserted_total += len(replayed)
        mismatches = []
        if original is not None:
            original_asserted = _asserted_map(original)
            keys = sorted(set(replayed) | set(original_asserted))
            for key in keys:
                if replayed.get(key) != original_asserted.get(key):
                    mismatches.append({
                        "obligation_id": key[0], "target": key[1],
                        "live_audit": original_asserted.get(key), "offline_replay": replayed.get(key),
                    })
        if mismatches:
            failures.extend({"model": model.name, "status": "replay_mismatch", **m} for m in mismatches)
        case_rows.append({
            "model": model.name,
            "status": "passed" if not mismatches else "replay_mismatch",
            "validated_target_count": imported["validated_target_count"],
            "completed_targets": sorted(t for t, r in reports.items() if r.get("status") == "completed"),
            "asserted_shared_verdict_count": len(replayed),
            "replay_mismatch_count": len(mismatches),
        })

    summary = {
        "format": "factgraph-portable-live-evidence-replay-v1",
        "case_count": len(case_rows),
        "asserted_shared_verdict_count": asserted_total,
        "failure_count": len(failures),
        "require_live": args.require_live,
        "passed": not failures,
        "rule": "A portable live-evidence artifact is accepted only when its semantic-model and witness-plan fingerprints validate and its asserted shared-semantic verdicts replay identically without target connections.",
        "cases": case_rows,
        "failures": failures,
    }
    out = args.out or (args.root / "PORTABLE_LIVE_EVIDENCE_REPLAY.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("case_count", "asserted_shared_verdict_count", "failure_count", "passed")}, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
