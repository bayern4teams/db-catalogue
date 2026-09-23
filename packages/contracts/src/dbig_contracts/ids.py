"""Stable identities derived from source-native identifiers."""

import hashlib


def stable_id(source_id: str, kind: str, native_id: str) -> str:
    if not source_id or not kind or not native_id:
        raise ValueError("identity components must be nonempty")
    payload = "\x00".join((source_id, kind, native_id)).encode("utf-8")
    return f"{kind}:{hashlib.sha256(payload).hexdigest()[:32]}"
