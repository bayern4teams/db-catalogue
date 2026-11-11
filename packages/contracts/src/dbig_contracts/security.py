"""Boundary validation shared by private model, telemetry and secret-reference callers."""

import hashlib
import hmac
import logging
import os
import socket
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from .errors import OperatorError


def cap_model_client_logs() -> None:
    """At DEBUG the model client's libraries log whole requests and replies (B1, AGENTS.md: no raw
    prompt or response tracing). Called where a model client is used, so no setup can undo it."""
    for name in ("openai", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


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


def private_host_refusal(host: str) -> str | None:
    """Why a database host is refused, or None when every address it names is private or
    loopback (report Part 3, layer L4; owner 2026-11-11: "Look up, all private"). A name is
    looked up, and one public address among its answers refuses it."""
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        addresses = [ip_address(candidate)]
    except ValueError:
        try:
            answers = socket.getaddrinfo(candidate, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return f"database host {host!r} does not resolve; check the name or use its address"
        # A link-local IPv6 answer carries a scope (`fe80::1%eth0`), which ip_address refuses.
        addresses = [ip_address(str(answer[4][0]).split("%", 1)[0]) for answer in answers]
    if not addresses or any(not (item.is_private or item.is_loopback) for item in addresses):
        return (
            f"database host {host!r} is not private: every address it names must be a private "
            "or loopback address"
        )
    return None


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
    file_reference = os.environ.get(f"{reference}_FILE")
    if value is None and file_reference:
        # A mounted secret file (report Part 3: secrets as files, not in the environment).
        try:
            value = Path(file_reference).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise SecretReferenceError(
                f"{purpose} reads the file named by {reference}_FILE, which cannot be read "
                f"({type(error).__name__}); mount it readable by the service user"
            ) from None
    if value is None:
        raise SecretReferenceError(
            f"{purpose} reads environment variable {reference}, which is not set; set it, or "
            f"set {reference}_FILE to a file that holds it"
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
    try:
        key = secret_from_env(reference, purpose=purpose).encode("utf-8")
    except UnicodeEncodeError:
        raise SecretReferenceError(
            f"environment variable {reference} for {purpose} is not valid text"
        ) from None
    if len(key) < MIN_KEY_BYTES:
        raise SecretReferenceError(
            f"environment variable {reference} for {purpose} must hold at least "
            f"{MIN_KEY_BYTES} bytes; generate a longer one"
        )
    return key


def bind_refusal(host: str, *, tls: bool) -> str | None:
    """Why `serve` must not bind `host`, as a one-line fix, or None when it may.

    The one bind rule for every service (report Part 3, layer L1): a loopback literal binds as
    before; a private IP literal binds only with TLS (owner decision D1, 2026-11-10: "TLS,
    private CA"), because a key sent in plain text can be read by any host on the LAN. A name,
    an all-interfaces address and a public address never bind.
    """
    if is_loopback_literal(host):
        return None
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        address = ip_address(candidate)
    except ValueError:
        return "bind to a loopback or private IP literal, not a name, which can resolve anywhere"
    if address.is_unspecified:
        return "never bind all interfaces (0.0.0.0 or ::); bind loopback or the one LAN address"
    if not address.is_private:
        return "bind only to a loopback or private LAN address"
    if not tls:
        return "without TLS only loopback binds; a LAN bind needs --tls-cert and --tls-key"
    return None


def serve_refusal(host: str, tls_cert: object | None, tls_key: object | None) -> str | None:
    """`bind_refusal` for a `serve` command's `--host`, `--tls-cert` and `--tls-key`."""
    if (tls_cert is None) != (tls_key is None):
        return "give both --tls-cert and --tls-key, or neither"
    return bind_refusal(host, tls=tls_cert is not None)


class CallerKey(BaseModel):
    """One caller's key, stored only as the SHA-256 of the key text (report Part 3, layer L5)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    caller: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: frozenset[str] = Field(min_length=1)
    expires_at: AwareDatetime


class _CallerKeyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[CallerKey] = Field(min_length=1)


ANY_SCOPE = "*"


def load_caller_keys(path: Path) -> list[CallerKey]:
    """The keys in one service's mounted key file (owner ruling D4: one file per service).

    The file is JSON, `{"keys": [{"caller", "sha256", "scopes", "expires_at"}]}`. A key rotates
    by listing a second hash for the same caller until the old one is removed or expires.
    """
    try:
        return _CallerKeyFile.model_validate_json(path.read_bytes()).keys
    except OSError as error:
        raise SecretReferenceError(
            f"caller key file {path} cannot be read ({type(error).__name__}); mount it read-only"
        ) from None
    except ValidationError as error:
        problems = sorted({".".join(map(str, item["loc"])) for item in error.errors()})
        raise SecretReferenceError(
            f"caller key file {path} is invalid at {', '.join(problems)}; each key needs a "
            "caller, a 64-hex sha256, at least one scope and an expires_at with a time zone"
        ) from None


def token_caller_key(token: str) -> CallerKey:
    """The single bearer token of earlier releases, as one key with every scope."""
    return CallerKey(
        caller="token",
        sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        scopes=frozenset({ANY_SCOPE}),
        expires_at=datetime.max.replace(tzinfo=UTC),
    )


def authenticate(
    keys: list[CallerKey], authorization: str | None, now: datetime | None = None
) -> CallerKey | None:
    """The unexpired key whose hash matches a `Bearer` header, or None. Every listed hash is
    compared, in constant time, so the time taken does not tell which one was close."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    supplied = hashlib.sha256(authorization[len("Bearer ") :].encode("utf-8")).hexdigest()
    moment = now or datetime.now(UTC)
    found = None
    for key in keys:
        if hmac.compare_digest(supplied, key.sha256) and key.expires_at > moment:
            found = key
    return found


def permits(key: CallerKey, scope: str) -> bool:
    return ANY_SCOPE in key.scopes or scope in key.scopes


KEYS_FILE_ENV = "DBIG_CALLER_KEYS_FILE"


def service_keys(token_env: str | None, *, purpose: str) -> list[CallerKey]:
    """The caller keys a service accepts: the key file named by `DBIG_CALLER_KEYS_FILE` when set
    (owner ruling D4), else the single bearer token of earlier releases from `token_env`."""
    keys_file = os.environ.get(KEYS_FILE_ENV)
    if keys_file:
        return load_caller_keys(Path(keys_file))
    return [token_caller_key(secret_from_env(token_env, purpose=purpose))]
