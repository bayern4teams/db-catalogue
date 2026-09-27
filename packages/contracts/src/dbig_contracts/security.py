"""Boundary validation shared by private model, telemetry and secret-reference callers."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit


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
    if parsed.scheme != "https" and not address.is_loopback:
        raise ValueError("non-loopback private endpoints require HTTPS")
    return value.rstrip("/")


class SecretReferenceError(ValueError):
    """A secret environment reference is missing, blank, unset or empty.

    Subclasses `ValueError` so existing callers and tests that expect a `ValueError` keep working,
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
