"""pyproject declares requires-python >= 3.11; keep the source importable there.

Python 3.12 (PEP 701) accepts backslashes inside f-string replacement fields;
3.11 rejects them with a SyntaxError at import time.  audit.py, migration.py and
migrations/base.py all shipped such an expression, so the audit module could
not be imported on the declared floor.  This guard tokenizes every source file
and fails if an f-string expression part contains a backslash.
"""

from __future__ import annotations

import io
import sys
import tokenize
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "factgraph"


def _backslash_in_fstring_expr(path: Path) -> list[int]:
    if sys.version_info < (3, 12):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
        return []
    hits: list[int] = []
    depth = 0
    toks = tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline)
    for tok in toks:
        if tok.type == tokenize.FSTRING_START:
            depth += 1
        elif tok.type == tokenize.FSTRING_END:
            depth -= 1
        elif depth and tok.type != tokenize.FSTRING_MIDDLE and "\\" in tok.string:
            hits.append(tok.start[0])
    return hits


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(SRC)))
def test_no_backslash_in_fstring_expression(path: Path):
    assert _backslash_in_fstring_expr(path) == [], f"{path}: backslash inside f-string expression (SyntaxError on 3.11)"
