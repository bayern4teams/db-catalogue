"""Boundary validation shared by private model, telemetry and secret-reference callers."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit

from .errors import OperatorError


def is_loopback_literal(host: str) -> bool:
    """True only for a loopback IP literal such as `127.0.0.1`, `::1` or `[::1]`.

    The one rule for every place that lets something cross only the local machine: plaintext
    database connections, plain-HTTP model endpoints and the `serve` bind address. A name is never
    loopback here, `localhost` included, because a name is whatever the resolver says it is.
    """
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ip_address(candidate).is_loopback
    except ValueError:
        return False


def private_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("endpoint cannot contain credentials, query, or fragment")
    try:
        address = ip_address(parsed.hostname)
    except ValueError as error:
        raise ValueError("private endpoint must use an IP literal") from error
    if not (address.is_private or address.is_loopback):
        raise ValueError("endpoint must resolve to a private address")
    if parsed.scheme != "https" and not is_loopback_literal(parsed.hostname):
        raise ValueError("non-loopback private endpoints require HTTPS")
    return value.rstrip("/")


class SecretReferenceError(OperatorError):
    """A secret environment reference is missing, blank, unset or empty.

    Subclasses `OperatorError`, and so still `ValueError`, so existing callers and tests keep
    working,
    while giving command line entry points a precise type to convert into a clean command error
    without also swallowing validation failures or programming errors.
    """


def secret_from_env(reference: str | None, *, purpose: str) -> str:
    """Resolve a secret by environment-variable reference, failing closed.

    Every component reads credentials and signing keys indirectly, by the *name* of an
    environment variable held in validated configuration. A missing or blank reference, an unset
    variable, or a variable set to whitespace must all stop the caller rather than silently
    degrade into an unsigned or unauthenticated path. The raised message names the variable so an
    operator can fix it, and never contains the secret's value.
    """
    if reference is None or not reference.strip():
        raise SecretReferenceError(
            f"{purpose} requires a non-empty environment variable reference"
        )
    value = os.environ.get(reference)
    if value is None:
        raise SecretReferenceError(
            f"{purpose} reads environment variable {reference}, which is not set"
        )
    if not value.strip():
        raise SecretReferenceError(
            f"environment variable {reference} for {purpose} is empty"
        )
    return value


MIN_KEY_BYTES = 32


def key_from_env(reference: str | None, *, purpose: str) -> bytes:
    """Resolve a signing or tokenising key by reference and require MIN_KEY_BYTES of it.

    A key that is too short is an operator mistake, so it is a one-line `SecretReferenceError`
    that names the variable and the length needed, never the key (user-approved item E1).
    """
    key = secret_from_env(reference, purpose=purpose).encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise SecretReferenceError(
            f"environment variable {reference} for {purpose} must hold at least "
            f"{MIN_KEY_BYTES} bytes; generate a longer one"
        )
    return key
