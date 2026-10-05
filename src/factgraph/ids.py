from __future__ import annotations

import hashlib
import re


def stable_id(kind: str, *parts: str) -> str:
    escaped = [p.replace("%", "%25").replace(":", "%3A") for p in parts]
    return f"{kind}:" + ":".join(escaped)


def explicit_id(kind: str, token: str) -> str:
    """Return a stable, name-independent semantic identifier.

    Explicit source identities deliberately live in a different namespace from
    the historical name-derived IDs.  This makes it possible to tell, from a
    normalized model alone, whether a declaration's identity is stable across
    renames without storing a second "was explicit" flag.
    """

    if not isinstance(token, str) or not token.strip():
        raise ValueError("identity token must be a non-empty string")
    return stable_id("uid", kind, token)


def explicit_token(kind: str, value: str) -> str | None:
    """Recover an explicit identity token when ``value`` is one of ours."""

    prefix = f"uid:{kind}:"
    if not value.startswith(prefix):
        return None
    raw = value[len(prefix) :]
    # stable_id uses a deliberately tiny escaping scheme.  Decode in reverse
    # order rather than applying a generic URL decoder to unrelated syntax.
    return raw.replace("%3A", ":").replace("%25", "%")


def resolved_id(kind: str, name: str, identity: str | None = None) -> str:
    """Use an explicit stable identity when present, legacy behavior otherwise."""

    return explicit_id(kind, identity) if identity is not None else stable_id(kind, name)


def slug(name: str) -> str:
    """Deterministic snake_case SQL/document identifier helper."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return re.sub(r"[^a-zA-Z0-9_]+", "_", s2).strip("_").lower()


def artifact_filename_token(value: str, *, prefix_limit: int = 72) -> str:
    """Return a bounded, collision-resistant token for generated artifact paths.

    Semantic IDs may contain escaped GUIDs, role sequences, and other content
    that can exceed common 255-byte filesystem component limits.  Generated
    filenames therefore use a readable sanitized prefix plus a deterministic
    SHA-256 suffix.  The complete semantic ID remains inside the artifact.
    """

    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "artifact"
    prefix = readable[:prefix_limit].rstrip("._-") or "artifact"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}__{digest}"
