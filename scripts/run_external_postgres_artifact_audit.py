#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from factgraph.cli import load  # noqa: E402
from factgraph.postgres_artifact import write_bundle  # noqa: E402

EXAMPLES = ROOT / "examples" / "external_targets" / "postgres"


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _value_observation(report: dict) -> str | None:
    rows = [r for r in report.get("results", []) if r.get("kind") == "value"]
    if len(rows) != 1:
        return None
    return rows[0].get("refined_observation") or rows[0].get("observed_preservation")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the external PostgreSQL semantic-artifact demonstration")
    p.add_argument("--postgres-dsn", default=os.environ.get("FACTGRAPH_POSTGRES_DSN"))
    p.add_argument("--out-dir", type=Path, default=ROOT / "artifacts" / "external_postgres_live")
    p.add_argument("--require-live", action="store_true")
    args = p.parse_args(argv)

    model = load(EXAMPLES / "source_value_range.fg")
    rows = []
    expected = {
        "value_range_preserved": "preserved_on_tested_cases",
        "value_range_weakened": "weakened",
    }
    for name in sorted(expected):
        d = EXAMPLES / name
        report = write_bundle(
            model,
            (d / "schema.sql").read_text(encoding="utf-8"),
            _load_json(d / "mapping.json"),
            args.out_dir / name,
            dsn=args.postgres_dsn,
        )
        observed = _value_observation(report)
        passed = report.get("status") == "completed" and observed == expected[name]
        rows.append({
            "artifact": name,
            "status": report.get("status"),
            "expected_value_range_observation": expected[name],
            "actual_value_range_observation": observed,
            "passed": passed if report.get("status") == "completed" else None,
        })

    live = all(row["status"] == "completed" for row in rows)
    summary = {
        "format": "factgraph-external-postgres-artifact-demo-v1",
        "live_observed": live,
        "passed": all(row["passed"] is True for row in rows) if live else None,
        "artifacts": rows,
        "claim": "The same source-semantic Age=-1 witness is expected to be prevented by the preserving external DDL and realized by the weakened external DDL; source-valid Age=0 and Age=130 probes must also be accepted before the preserving artifact is refined to preserved_on_tested_cases.",
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.require_live and not live:
        return 2
    if live and summary["passed"] is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
