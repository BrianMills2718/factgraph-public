#!/usr/bin/env python3
"""Write deterministic SHA-256 project manifests, excluding self-referential files."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from factgraph import __version__  # noqa: E402

EXCLUDE_NAMES = {"PROJECT_FILE_MANIFEST.json", "FILE_MANIFEST.sha256", "HASH_MANIFEST_RESULTS.txt", "HASH_MANIFEST_GENERATION.jsonl"}
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}

items=[]
for p in sorted(ROOT.rglob('*')):
    if not p.is_file():
        continue
    rel=p.relative_to(ROOT)
    if p.name in EXCLUDE_NAMES or any(part in EXCLUDE_DIRS for part in rel.parts):
        continue
    b=p.read_bytes()
    items.append({"path": rel.as_posix(), "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()})

manifest={
    "version": __version__,
    "algorithm": "sha256",
    "excluded_self_referential_files": sorted(EXCLUDE_NAMES),
    "file_count": len(items),
    "files": items,
}
(ROOT/'PROJECT_FILE_MANIFEST.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n', encoding='utf-8')
(ROOT/'FILE_MANIFEST.sha256').write_text(''.join(f"{i['sha256']}  {i['path']}\n" for i in items), encoding='utf-8')
print(json.dumps({"version": __version__, "file_count": len(items)}, sort_keys=True))
