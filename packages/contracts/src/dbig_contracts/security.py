"""Boundary validation shared by private model and telemetry clients."""

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
