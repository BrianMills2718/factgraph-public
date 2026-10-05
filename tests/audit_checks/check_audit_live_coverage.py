"""Independent check for ChatGPT round 2 (chat 6abb9bee): partial legacy live coverage.

Run from a repo checkout:  PYTHONPATH=src python3 tests/audit_checks/check_audit_live_coverage.py

Gives every runtime conformance case a second sibling case, then audits
examples/richer_constraints.fg with a "completed" legacy live report that
passes only the first case of each obligation.  No obligation may be promoted
to an observed verdict on that partial evidence; with the report completed it
must be.  Exit 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
if not (root / "src" / "factgraph" / "audit.py").is_file():
    sys.exit(f"FAIL: expected repo source under {root}; run this file from tests/audit_checks/ in a checkout")

from factgraph import audit  # noqa: E402
from factgraph.normalize import normalize_model  # noqa: E402
from factgraph.parser import parse_model  # noqa: E402

OBSERVED = {"preserved_observed", "weakened_observed"}
original = audit._cases
audit._cases = lambda m: {
    t: [*cs, *(dataclasses.replace(c, id=c.id + "#second") for c in cs if c.mode == "runtime")]
    for t, cs in original(m).items()
}
model = normalize_model(parse_model((root / "examples" / "richer_constraints.fg").read_text()))
ids = sorted(
    c["case_id"]
    for ob in audit.build_audit(model, targets=["postgres"])["obligations"]
    for c in ob["verdicts"]["postgres"]["cases"]
    if c["mode"] == "runtime"
)


def observed(case_ids):
    live = {"postgres": {"target": "postgres", "status": "completed", "passed": True,
                         "results": [{"case_id": i, "mode": "runtime", "passed": True} for i in case_ids]}}
    rep = audit.build_audit(model, targets=["postgres"], live_reports=live)
    return sorted(ob["id"] for ob in rep["obligations"] if ob["verdicts"]["postgres"]["preservation"] in OBSERVED)


partial = observed([i for i in ids if not i.endswith("#second")] + ["pg-unrelated-extra-case"])
complete = observed(ids)
ok_partial = partial == []
ok_complete = len(complete) > 0
print(("PASS" if ok_partial else "FAIL") + f" partial coverage: {len(partial)} obligation(s) promoted to observed {partial[:3]}")
print(("PASS" if ok_complete else "FAIL") + f" complete coverage: {len(complete)} obligation(s) observed")
print("RESULT:", "PASS" if ok_partial and ok_complete else "FAIL")
sys.exit(0 if ok_partial and ok_complete else 1)
