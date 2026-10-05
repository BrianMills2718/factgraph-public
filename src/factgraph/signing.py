from __future__ import annotations

"""Optional Ed25519 attestations for immutable repository revisions.

Signatures are append-only attestations stored outside revision directories, so
adding a signature never mutates the immutable revision being signed.  The
module imports ``cryptography`` lazily; the core compiler/repository continues
to work without the optional signing dependency.
"""

import base64
import hashlib
import json
from pathlib import Path
from typing import Any


class SigningUnavailable(RuntimeError):
    pass


class SignatureError(ValueError):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except Exception as exc:  # pragma: no cover - depends on optional env
        raise SigningUnavailable(
            "revision signing requires the optional 'cryptography' package"
        ) from exc
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def key_id_from_public_key(public_key) -> str:
    serialization, _, _ = _crypto()
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "ed25519:" + hashlib.sha256(raw).hexdigest()[:24]


def generate_keypair(private_path: Path, public_path: Path) -> dict[str, str]:
    serialization, Ed25519PrivateKey, _ = _crypto()
    private = Ed25519PrivateKey.generate()
    private_path = Path(private_path)
    public_path = Path(public_path)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public = private.public_key()
    public_path.write_bytes(
        public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return {
        "algorithm": "ed25519",
        "key_id": key_id_from_public_key(public),
        "private_key": str(private_path),
        "public_key": str(public_path),
    }


def load_private_key(path: Path):
    serialization, _, Ed25519PublicKey = _crypto()
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if key.__class__.__name__ != "Ed25519PrivateKey":
        # isinstance is awkward across backend proxy classes; behavior is enough.
        try:
            key.sign(b"")
            raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            Ed25519PublicKey.from_public_bytes(raw)
        except Exception as exc:
            raise SignatureError("private key is not Ed25519") from exc
    return key


def load_public_key(path: Path):
    serialization, _, _ = _crypto()
    key = serialization.load_pem_public_key(Path(path).read_bytes())
    try:
        key.verify(b"", b"")
    except TypeError:
        raise SignatureError("public key is not Ed25519")
    except Exception:
        # Invalid empty signature is expected for an Ed25519 key.
        pass
    return key


def sign_payload(payload: dict[str, Any], private_key_path: Path) -> tuple[str, str, bytes]:
    private = load_private_key(private_key_path)
    public = private.public_key()
    raw = canonical_json_bytes(payload)
    signature = private.sign(raw)
    return key_id_from_public_key(public), base64.b64encode(signature).decode("ascii"), raw


def verify_payload(payload: dict[str, Any], signature_b64: str, public_key_path: Path) -> None:
    public = load_public_key(public_key_path)
    signature = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    public.verify(signature, canonical_json_bytes(payload))
