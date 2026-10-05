"""Independent check for the ChatGPT re-review finding (chat 6abb8160).

Run from a repo checkout:  PYTHONPATH=src python3 tests/audit_checks/check_constraint_element_refs.py

Builds a fact with explicit fact and role identities, creates every
fact-referencing constraint (and a reading) through the public model
factories, and checks that each referenced fact/role id exists on the fact.
It calls the element-taking API when available and falls back to the older
name-taking API, so it runs (and discriminates) on both sides of the fix.
Also checks the source has no backslash inside an f-string expression
(SyntaxError on the declared Python 3.11 floor).  Exit 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

from factgraph.model import Constraint, ConstraintKind, EntityType, FactType, Reading

person = EntityType.create("Person", identity="p")
works = FactType.create("Works", [("a", person.id, "works-a"), ("b", person.id, "works-b")], identity="custom")
other = FactType.create("Other", [("a", person.id, "other-a"), ("b", person.id, "other-b")], identity="custom2")
facts = {works.id: works, other.id: other}


def call(new, old):
    try:
        return new()
    except (TypeError, AttributeError):
        return old()


cases = {
    "uniqueness": lambda: call(lambda: Constraint.uniqueness(works, ["a"]), lambda: Constraint.uniqueness("Works", ["a"])),
    "mandatory": lambda: call(lambda: Constraint.mandatory(works, "a"), lambda: Constraint.mandatory("Works", "a")),
    "frequency": lambda: call(lambda: Constraint.frequency(works, ["a"], 1, 2), lambda: Constraint.frequency("Works", ["a"], 1, 2)),
    "unordered": lambda: call(lambda: Constraint.unordered(works, ["a", "b"]), lambda: Constraint.unordered("Works", ["a", "b"])),
    "ring": lambda: call(lambda: Constraint.ring(works, "symmetric"), lambda: Constraint.ring("Works", "symmetric")),
    "role_set": lambda: call(
        lambda: Constraint.role_set(ConstraintKind.SUBSET, works, ["a"], other, ["a"]),
        lambda: Constraint.role_set(ConstraintKind.SUBSET, "Works", ["a"], "Other", ["a"]),
    ),
    "reading": lambda: call(
        lambda: Reading.create(works, "{a} works with {b}", (works.role("a").id, works.role("b").id)),
        lambda: Reading.create("Works", "{a} works with {b}", (works.role("a").id, works.role("b").id)),
    ),
}

failures = 0
for name, make in cases.items():
    el = make()
    problems = []
    pairs = [(el.fact_type_id, el.role_ids)]
    if getattr(el, "target_fact_type_id", None) is not None:
        pairs.append((el.target_fact_type_id, el.target_role_ids))
    for fid, rids in pairs:
        fact = facts.get(fid)
        if fact is None:
            problems.append(f"fact {fid!r} does not exist")
            continue
        missing = [r for r in rids if r not in {x.id for x in fact.roles}]
        if missing:
            problems.append(f"role(s) {missing} not on {fid}")
    status = "PASS" if not problems else "FAIL"
    failures += bool(problems)
    print(f"{status} {name}: {'; '.join(problems) or 'references resolve'}")

src = Path(__file__).resolve().parents[2] / "src" / "factgraph"
if not (src / "model.py").is_file():
    # Fail loudly rather than vacuously scanning nothing when run from a copy.
    sys.exit(f"FAIL: expected repo source at {src}; run this file from tests/audit_checks/ in a checkout")
hits = []
if sys.version_info >= (3, 12):
    for path in sorted(src.rglob("*.py")):
        depth = 0
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if tok.type == tokenize.FSTRING_START:
                depth += 1
            elif tok.type == tokenize.FSTRING_END:
                depth -= 1
            elif depth and tok.type != tokenize.FSTRING_MIDDLE and "\\" in tok.string:
                hits.append(f"{path.relative_to(src)}:{tok.start[0]}")
print(("FAIL" if hits else "PASS") + f" py311-fstring-floor: {hits or 'no backslash in f-string expressions'}")
failures += bool(hits)

print("RESULT:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
