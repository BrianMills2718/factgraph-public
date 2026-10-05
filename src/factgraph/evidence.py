from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .model import Model
from .shared_witness_execution import evidence_identity


class EvidenceValidationError(ValueError):
    """Raised when imported live evidence cannot be bound to the current audit plan."""


def validate_shared_live_report(model: Model, target: str, report: dict[str, Any]) -> dict[str, Any]:
    """Validate portable shared-witness evidence against the exact current model/plan.

    v0.13 intentionally requires the v2 evidence format for imported evidence. Older
    v1 reports can still be read as historical files, but they do not contain enough
    provenance to be allowed to change a current semantic-preservation verdict.
    """
    if report.get("format") != "factgraph-shared-witness-live-v2":
        raise EvidenceValidationError(
            f"{target}: imported shared-witness evidence must use factgraph-shared-witness-live-v2"
        )
    if report.get("target") != target:
        raise EvidenceValidationError(
            f"{target}: evidence target mismatch: found {report.get('target')!r}"
        )
    expected = evidence_identity(model, target)
    for field, value in expected.items():
        actual = report.get(field)
        if actual != value:
            raise EvidenceValidationError(
                f"{target}: evidence {field} mismatch: expected {value!r}, found {actual!r}"
            )

    results = report.get("results")
    if not isinstance(results, list):
        raise EvidenceValidationError(f"{target}: evidence results must be a list")
    ids: set[str] = set()
    for row in results:
        if not isinstance(row, dict):
            raise EvidenceValidationError(f"{target}: evidence result rows must be objects")
        oid = row.get("obligation_id")
        if not isinstance(oid, str) or not oid:
            raise EvidenceValidationError(f"{target}: evidence result missing obligation_id")
        if oid in ids:
            raise EvidenceValidationError(f"{target}: duplicate evidence result for obligation {oid}")
        ids.add(oid)
        row_target = row.get("target")
        if row_target not in {None, target}:
            raise EvidenceValidationError(
                f"{target}: result {oid} claims target {row_target!r}"
            )

    if report.get("status") == "completed":
        expected_count = int(report.get("case_count", len(results)))
        if expected_count != len(results):
            raise EvidenceValidationError(
                f"{target}: completed evidence case_count={expected_count} but contains {len(results)} results"
            )
    return report


def load_shared_live_report(path: Path, model: Model, target: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvidenceValidationError(f"{target}: evidence file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceValidationError(f"{target}: invalid evidence JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise EvidenceValidationError(f"{target}: evidence root must be an object")
    return validate_shared_live_report(model, target, raw)


def load_shared_live_directory(path: Path, model: Model, targets: Iterable[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for target in targets:
        file = path / f"{target}.json"
        if not file.exists():
            rows.append({"target": target, "status": "missing", "file": str(file)})
            continue
        report = load_shared_live_report(file, model, target)
        reports[target] = report
        rows.append({
            "target": target,
            "status": "validated",
            "file": str(file),
            "live_status": report.get("status"),
            "model_semantic_sha256": report.get("model_semantic_sha256"),
            "case_plan_sha256": report.get("case_plan_sha256"),
        })
    return reports, {
        "format": "factgraph-shared-witness-evidence-import-v1",
        "directory": str(path),
        "validated_target_count": len(reports),
        "rows": rows,
        "rule": "Imported evidence may affect an audit verdict only after its semantic-model and witness-plan fingerprints match the current generated plan.",
    }
