#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
ART="$ROOT/artifacts"
V08="$ROOT/examples/repository_v08"
COLLAB="$ART/collaboration_demo"
AUTHOR="$COLLAB/author"
HUB="$COLLAB/hub"
REVIEWER="$COLLAB/reviewer"
rm -rf "$COLLAB"
mkdir -p "$COLLAB"

python -m factgraph repo-init "$AUTHOR" --name "CollaborationAuthor" --out "$COLLAB/author.init.json"
python -m factgraph repo-init "$HUB" --name "CollaborationHub" --out "$COLLAB/hub.init.json"
python -m factgraph repo-init "$REVIEWER" --name "CollaborationReviewer" --out "$COLLAB/reviewer.init.json"
python -m factgraph repo-commit "$AUTHOR" "$V08/base.fg" --model-key team-app-v09 \
  --author "Ada" --message "collaboration base" --out "$COLLAB/author.commit.json"
python -m factgraph repo-remote "$AUTHOR" origin "$HUB" --out "$COLLAB/author.remote.json"
python -m factgraph repo-remote "$REVIEWER" origin "$HUB" --out "$COLLAB/reviewer.remote.json"

python - "$COLLAB/demo.private.pem" "$COLLAB/demo.public.pem" <<'PY'
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
python -m factgraph repo-trust-key "$HUB" "$COLLAB/demo.public.pem" --label "deterministic collaboration demo key" --out "$COLLAB/hub.trust-key.json"
KEY_ID="$(python - "$COLLAB/hub.trust-key.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))['key_id'])
PY
)"
python -m factgraph repo-protect-branch "$HUB" team-app-v09 main --min-signatures 1 --key-id "$KEY_ID" --out "$COLLAB/hub.protect-main.json"

set +e
python -m factgraph repo-push "$AUTHOR" origin team-app-v09 --branch main --out-dir "$COLLAB/push.unsigned"
UNSIGNED_RC=$?
set -e
if [ "$UNSIGNED_RC" -ne 2 ]; then
  echo "expected unsigned protected push to fail with exit 2, got $UNSIGNED_RC" >&2
  exit 1
fi
printf '%s\n' "$UNSIGNED_RC" > "$COLLAB/push.unsigned/EXIT_CODE.txt"

python -m factgraph repo-sign "$AUTHOR" team-app-v09 --ref main --private-key "$COLLAB/demo.private.pem" \
  --signer "Factgraph collaboration demo" --out "$COLLAB/author.signature.json"
rm -f "$COLLAB/demo.private.pem"
python -m factgraph repo-push "$AUTHOR" origin team-app-v09 --branch main --out-dir "$COLLAB/push.signed"
python -m factgraph repo-push "$AUTHOR" origin team-app-v09 --branch main --out-dir "$COLLAB/push.dedup"
python -m factgraph repo-fetch "$REVIEWER" origin team-app-v09 --branch main --out-dir "$COLLAB/reviewer.fetch"
python -m factgraph repo-refs "$REVIEWER" team-app-v09 --out "$COLLAB/reviewer.refs.json"
python -m factgraph repo-trust-evaluate "$HUB" team-app-v09 --ref main --branch main --out "$COLLAB/hub.trust-evaluation.json"
python -m factgraph repo-verify "$HUB" --out "$COLLAB/hub.verification.json"

python -m factgraph repo-branch "$AUTHOR" team-app-v09 explain/ours --from-ref main --out "$COLLAB/explain.branch.ours.json"
python -m factgraph repo-branch "$AUTHOR" team-app-v09 explain/theirs --from-ref main --out "$COLLAB/explain.branch.theirs.json"
python -m factgraph repo-commit "$AUTHOR" "$V08/conflict_ours.fg" --model-key team-app-v09 --branch explain/ours --out "$COLLAB/explain.commit.ours.json"
python -m factgraph repo-commit "$AUTHOR" "$V08/conflict_theirs.fg" --model-key team-app-v09 --branch explain/theirs --out "$COLLAB/explain.commit.theirs.json"
set +e
python -m factgraph repo-merge "$AUTHOR" team-app-v09 --ours explain/ours --theirs explain/theirs --out-dir "$COLLAB/explain.merge"
EXPLAIN_RC=$?
set -e
if [ "$EXPLAIN_RC" -ne 2 ]; then
  echo "expected explained merge conflict exit 2, got $EXPLAIN_RC" >&2
  exit 1
fi

python - "$COLLAB" <<'PY'
from pathlib import Path
import json, sys
c=Path(sys.argv[1])
def read(path): return json.loads((c/path).read_text())
unsigned=read('push.unsigned/transfer.json')
signed=read('push.signed/transfer.json')
dedup=read('push.dedup/transfer.json')
fetch=read('reviewer.fetch/transfer.json')
refs=read('reviewer.refs.json')
trust=read('hub.trust-evaluation.json')
verify=read('hub.verification.json')
merge=read('explain.merge/merge.json')
explanation=(c/'explain.merge/merge.explanation.md').read_text()
summary={
  'format':'factgraph-v0.9-collaboration-demo-v1',
  'unsigned_push_status':unsigned['status'],
  'unsigned_push_error':unsigned['error'],
  'signed_push_status':signed['status'],
  'signed_pack_object_count':signed['pack']['object_count'],
  'dedup_push_object_count':dedup['pack']['object_count'],
  'fetch_status':fetch['status'],
  'fetch_local_branch_advanced':fetch['local_branch_advanced'],
  'reviewer_local_branches':refs['branches'],
  'reviewer_remote_tracking':refs['remote_tracking'],
  'protected_branch_trust_passed':trust['passed'],
  'accepted_signature_count':trust['accepted_signature_count'],
  'hub_repository_verification_passed':verify['passed'],
  'merge_conflict_count':merge['merge']['summary']['conflict_count'],
  'merge_has_orm_explanation':('population semantics' in explanation and 'Object Type' in explanation),
}
(c/'V0_9_COLLABORATION_SUMMARY.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
print(json.dumps(summary,sort_keys=True))
PY
