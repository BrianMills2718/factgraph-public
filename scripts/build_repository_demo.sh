#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
ART="$ROOT/artifacts"
META="$ART/metamodel_versions"
REPO_ART="$ART/repository_demo"
REPO="$REPO_ART/repository"
BASE="$ROOT/examples/migrations/safe_risky"

rm -rf "$META" "$REPO_ART"
mkdir -p "$META" "$REPO_ART"

python -m factgraph metamodel-versions --out "$META/versions.json"
python -m factgraph metamodel-diff 1 2 --out-dir "$META/v1_to_v2_diff"
python -m factgraph reify "$ROOT/examples/warehouse.fg" --metamodel-version 1 --out-dir "$META/warehouse_v1"
python -m factgraph metamodel-migrate "$META/warehouse_v1/envelope.population.json" \
  --to-version 2 --out-dir "$META/warehouse_v1_to_v2"

python -m factgraph repo-init "$REPO" --name "FactgraphDemoRepository" --metamodel-version 1 --out "$REPO_ART/init.json"
python -m factgraph repo-commit "$REPO" "$BASE/before.fg" --model-key team-app --metamodel-version 1 --out "$REPO_ART/commit.before.json"
python -m factgraph repo-commit "$REPO" "$BASE/after.fg" --model-key team-app --metamodel-version 1 --out "$REPO_ART/commit.after.json"

read -r BEFORE AFTER < <(python - "$REPO_ART/commit.before.json" "$REPO_ART/commit.after.json" <<'PY'
import json, sys
b=json.load(open(sys.argv[1]))
a=json.load(open(sys.argv[2]))
print(b['revision_id'], a['revision_id'])
PY
)
python -m factgraph repo-diff "$REPO" team-app "$BEFORE" "$AFTER" --out-dir "$REPO_ART/domain_diff"
python -m factgraph repo-migrate-metamodel "$REPO" --to-version 2 --model-key team-app --out "$REPO_ART/metamodel_migration.json"
python -m factgraph repo-list "$REPO" --out "$REPO_ART/models.json"
python -m factgraph repo-log "$REPO" team-app --out "$REPO_ART/team-app.log.json"
python -m factgraph repo-verify "$REPO" --out "$REPO_ART/verification.json"
python -m factgraph repo-checkout "$REPO" team-app --out-dir "$REPO_ART/head_checkout"

python - "$META" "$REPO_ART" <<'PY'
from pathlib import Path
import json, sys
meta=Path(sys.argv[1]); repo=Path(sys.argv[2])
versions=json.loads((meta/'versions.json').read_text())
mdiff=json.loads((meta/'v1_to_v2_diff'/'SUMMARY.json').read_text())
pmig=json.loads((meta/'warehouse_v1_to_v2'/'migration.json').read_text())
verify=json.loads((repo/'verification.json').read_text())
log=json.loads((repo/'team-app.log.json').read_text())
summary={
  'format':'factgraph-v0.7-repository-demo-v1',
  'metamodel_versions':[x['version'] for x in versions['versions']],
  'current_metamodel_version':versions['current_version'],
  'metamodel_diff_change_count':mdiff['diff']['change_count'],
  'standalone_population_migration_passed':pmig['migration_passed'],
  'repository_verification_passed':verify['passed'],
  'repository_revision_count':len(log['revisions']),
  'repository_head_metamodel_version':log['revisions'][-1]['metamodel_version'],
  'repository_operations':[x['operation'] for x in log['revisions']],
}
(repo/'V0_7_REPOSITORY_DEMO_SUMMARY.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
print(json.dumps(summary,sort_keys=True))
PY

# v0.8 repository evolution demo: stable identities, branches/tags, semantic
# merge, explicit conflict resolution, provenance, and append-only signatures.
V08="$ROOT/examples/repository_v08"
V08_KEY="team-app-v08"

python -m factgraph repo-commit "$REPO" "$V08/base.fg" --model-key "$V08_KEY" \
  --author "Ada" --message "stable identity base" --out "$REPO_ART/v08.commit.base.json"
python -m factgraph repo-branch "$REPO" "$V08_KEY" feature/person --from-ref main --out "$REPO_ART/v08.branch.feature.json"
python -m factgraph repo-tag "$REPO" "$V08_KEY" stable-v1 --ref main --out "$REPO_ART/v08.tag.base.json"
python -m factgraph repo-commit "$REPO" "$V08/main.fg" --model-key "$V08_KEY" --branch main \
  --author "Ada" --message "add display name" --out "$REPO_ART/v08.commit.main.json"
python -m factgraph repo-commit "$REPO" "$V08/feature_person.fg" --model-key "$V08_KEY" --branch feature/person \
  --author "Lin" --message "rename User to Person without changing identity" --out "$REPO_ART/v08.commit.feature.json"
python -m factgraph repo-merge "$REPO" "$V08_KEY" --ours main --theirs feature/person \
  --out-dir "$REPO_ART/v08.merge.plan"
python -m factgraph repo-merge "$REPO" "$V08_KEY" --ours main --theirs feature/person --commit \
  --author "Merge Bot" --message "semantic merge of stable rename" --out-dir "$REPO_ART/v08.merge.commit"
python -m factgraph repo-tag "$REPO" "$V08_KEY" merged-v1 --ref main --out "$REPO_ART/v08.tag.merged.json"

# Deterministic demonstration key only. It exists solely so release artifacts are
# reproducible; it is not a production credential and the private key is removed
# after the signature is produced.
python - "$REPO_ART/v08.demo.private.pem" "$REPO_ART/v08.demo.public.pem" <<'PY'
from pathlib import Path
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
private = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
Path(sys.argv[1]).write_bytes(private.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
))
Path(sys.argv[2]).write_bytes(private.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
))
PY
python -m factgraph repo-sign "$REPO" "$V08_KEY" --ref main --private-key "$REPO_ART/v08.demo.private.pem" \
  --signer "Factgraph deterministic demo key" --out "$REPO_ART/v08.signature.json"
rm -f "$REPO_ART/v08.demo.private.pem"
python -m factgraph repo-verify-signatures "$REPO" --model-key "$V08_KEY" --out "$REPO_ART/v08.signature.verify.json"
# A second pair of branches demonstrates an actual modify/modify conflict and a
# file-carried assisted resolution.
python -m factgraph repo-branch "$REPO" "$V08_KEY" conflict/ours --from-ref stable-v1 --out "$REPO_ART/v08.branch.conflict.ours.json"
python -m factgraph repo-branch "$REPO" "$V08_KEY" conflict/theirs --from-ref stable-v1 --out "$REPO_ART/v08.branch.conflict.theirs.json"
python -m factgraph repo-commit "$REPO" "$V08/conflict_ours.fg" --model-key "$V08_KEY" --branch conflict/ours \
  --message "conflicting Text scalar: Int" --out "$REPO_ART/v08.commit.conflict.ours.json"
python -m factgraph repo-commit "$REPO" "$V08/conflict_theirs.fg" --model-key "$V08_KEY" --branch conflict/theirs \
  --message "conflicting Text scalar: Bool" --out "$REPO_ART/v08.commit.conflict.theirs.json"
set +e
python -m factgraph repo-merge "$REPO" "$V08_KEY" --ours conflict/ours --theirs conflict/theirs \
  --out-dir "$REPO_ART/v08.merge.conflict"
MERGE_CONFLICT_STATUS=$?
set -e
if [ "$MERGE_CONFLICT_STATUS" -ne 2 ]; then
  echo "expected semantic merge conflict exit status 2, got $MERGE_CONFLICT_STATUS" >&2
  exit 1
fi
python - "$REPO_ART/v08.merge.conflict/merge.json" "$REPO_ART/v08.merge.resolutions.json" <<'PY'
from pathlib import Path
import json, sys
report=json.loads(Path(sys.argv[1]).read_text())
conflicts=report['merge']['conflicts']
if not conflicts:
    raise SystemExit('expected at least one conflict')
res={'conflicts': {c['id']: 'ours' for c in conflicts}}
Path(sys.argv[2]).write_text(json.dumps(res, indent=2, sort_keys=True)+'\n')
PY
python -m factgraph repo-merge "$REPO" "$V08_KEY" --ours conflict/ours --theirs conflict/theirs \
  --resolutions "$REPO_ART/v08.merge.resolutions.json" --out-dir "$REPO_ART/v08.merge.resolved"

python -m factgraph repo-verify "$REPO" --out "$REPO_ART/verification.json"
# Capture refs/log only after all demo branches have been populated so the
# release evidence reflects the complete repository DAG.
python -m factgraph repo-refs "$REPO" "$V08_KEY" --out "$REPO_ART/v08.refs.json"
python -m factgraph repo-log "$REPO" "$V08_KEY" --out "$REPO_ART/v08.log.json"

python - "$REPO_ART" <<'PY'
from pathlib import Path
import json, sys
r=Path(sys.argv[1])
def read(name): return json.loads((r/name).read_text())
clean=read('v08.merge.commit/merge.json')
conflict=read('v08.merge.conflict/merge.json')
resolved=read('v08.merge.resolved/merge.json')
refs=read('v08.refs.json')
log=read('v08.log.json')
sig=read('v08.signature.verify.json')
verify=read('verification.json')
summary={
  'format':'factgraph-v0.8-repository-evolution-demo-v1',
  'stable_identity_model_key':'team-app-v08',
  'clean_merge_status':clean['merge']['status'],
  'clean_merge_conflicts':clean['merge']['summary']['conflict_count'],
  'clean_merge_parent_count':len(clean['commit']['parent_revision_ids']),
  'conflict_merge_status':conflict['merge']['status'],
  'conflict_count':conflict['merge']['summary']['conflict_count'],
  'resolved_merge_status':resolved['merge']['status'],
  'applied_resolution_count':resolved['merge']['summary']['applied_resolution_count'],
  'branches':refs['branches'],
  'tags':refs['tags'],
  'revision_count':len(log['revisions']),
  'merge_commit_count':sum(x.get('operation')=='merge' for x in log['revisions']),
  'signature_verification_passed':sig['passed'],
  'attestation_count':sig['attestation_count'],
  'repository_verification_passed':verify['passed'],
  'repository_verification_errors':verify['errors'],
}
(r/'V0_8_REPOSITORY_EVOLUTION_SUMMARY.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
print(json.dumps(summary,sort_keys=True))
PY


"$ROOT/scripts/build_collaboration_demo.sh"
