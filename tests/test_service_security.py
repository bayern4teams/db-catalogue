"""Report Part 3 (owner D1, 2026-11-10): one bind rule and one per-caller key check for every
service. T2: a LAN bind needs an IP literal, a private address and TLS. T3 (unit part): a key is
32 random bytes stored only as its SHA-256, with scopes and an expiry; two hashes for one caller
let a key rotate."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from ipaddress import IPv4Address

import pytest
from dbig_contracts.security import (
    SecretReferenceError,
    authenticate,
    bind_refusal,
    load_caller_keys,
    permits,
    secret_from_env,
    token_caller_key,
)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]"])
def test_loopback_binds_without_tls(host):
    assert bind_refusal(host, tls=False) is None


@pytest.mark.parametrize("host", ["10.20.30.40", "192.168.1.5", "172.30.0.10"])
def test_a_private_address_binds_only_with_tls(host):
    assert "--tls-cert" in bind_refusal(host, tls=False)
    assert bind_refusal(host, tls=True) is None


@pytest.mark.parametrize(
    ("host", "word"),
    [(str(IPv4Address(0)), "all interfaces"), ("::", "all interfaces"), ("8.8.8.8", "private"),
     ("2001:4860::8888", "private"), ("db.example.lan", "IP literal"), ("localhost", "IP literal")],
)
def test_public_unspecified_and_named_hosts_never_bind(host, word):
    assert word in bind_refusal(host, tls=True)


def _key_file(tmp_path, entries):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": entries}), encoding="utf-8")
    return path


def _entry(token, *, caller="pipeline", scopes=("search",), days=30):
    return {
        "caller": caller,
        "sha256": hashlib.sha256(token.encode()).hexdigest(),
        "scopes": list(scopes),
        "expires_at": (datetime.now(UTC) + timedelta(days=days)).isoformat(),
    }


def test_a_listed_key_authenticates_and_carries_its_scopes(tmp_path):
    token = secrets.token_urlsafe(32)
    keys = load_caller_keys(_key_file(tmp_path, [_entry(token)]))
    key = authenticate(keys, f"Bearer {token}")
    assert key is not None and key.caller == "pipeline"
    assert permits(key, "search")
    assert not permits(key, "scan")


@pytest.mark.parametrize("header", [None, "", "Bearer ", "Bearer wrong", "Basic abc"])
def test_a_missing_or_wrong_key_authenticates_nothing(tmp_path, header):
    keys = load_caller_keys(_key_file(tmp_path, [_entry(secrets.token_urlsafe(32))]))
    assert authenticate(keys, header) is None


def test_an_expired_key_authenticates_nothing(tmp_path):
    token = secrets.token_urlsafe(32)
    keys = load_caller_keys(_key_file(tmp_path, [_entry(token, days=-1)]))
    assert authenticate(keys, f"Bearer {token}") is None


def test_two_hashes_for_one_caller_both_work_while_a_key_rotates(tmp_path):
    old, new = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    keys = load_caller_keys(_key_file(tmp_path, [_entry(old), _entry(new)]))
    assert authenticate(keys, f"Bearer {old}").caller == "pipeline"
    assert authenticate(keys, f"Bearer {new}").caller == "pipeline"


@pytest.mark.parametrize(
    "entry",
    [{"caller": "x", "sha256": "abc", "scopes": ["search"], "expires_at": "2030-01-01T00:00:00Z"},
     {"caller": "x", "sha256": "0" * 64, "scopes": [], "expires_at": "2030-01-01T00:00:00Z"},
     {"caller": "x", "sha256": "0" * 64, "scopes": ["search"],
      "expires_at": "2030-01-01T00:00:00"}],
)
def test_a_malformed_key_file_is_a_one_line_error_naming_the_file(tmp_path, entry):
    path = _key_file(tmp_path, [entry])
    with pytest.raises(SecretReferenceError, match="keys.json"):
        load_caller_keys(path)


def test_the_single_token_of_earlier_releases_is_one_key_with_every_scope():
    token = secrets.token_urlsafe(32)
    key = authenticate([token_caller_key(token)], f"Bearer {token}")
    assert key is not None and permits(key, "anything")


def test_a_secret_can_come_from_a_mounted_file(tmp_path, monkeypatch):
    secret = tmp_path / "db_password"
    secret.write_text("from-a-file\n", encoding="utf-8")
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.setenv("DB_PASSWORD_FILE", str(secret))
    assert secret_from_env("DB_PASSWORD", purpose="database password") == "from-a-file"


def test_an_unset_secret_names_both_ways_to_set_it(monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.delenv("DB_PASSWORD_FILE", raising=False)
    with pytest.raises(SecretReferenceError, match="DB_PASSWORD_FILE"):
        secret_from_env("DB_PASSWORD", purpose="database password")


# Review findings 8 and 9 (owner "Yes, fix all", 2026-11-12), on the guard itself over ASGI.
def _guard(tokens, **limits):
    import asyncio

    from dbig_contracts.security import CallerKey
    from dbig_contracts.service import ServiceGuard

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    keys = [CallerKey(caller=name, sha256=hashlib.sha256(token.encode()).hexdigest(),
                      scopes=frozenset({"search"}), expires_at=datetime.now(UTC) + timedelta(1))
            for name, token in tokens.items()]
    guard = ServiceGuard(inner, keys=keys, route_scopes={}, max_request_bytes=16, **limits)

    def call(token, chunks, headers=()):
        messages = iter([{"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
                         for i, chunk in enumerate(chunks)])
        sent = []

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "path": "/v1/x", "method": "POST",
                 "headers": [(b"authorization", f"Bearer {token}".encode()), *headers]}
        asyncio.run(guard(scope, receive, send))
        return sent[0]["status"]

    return call


def test_a_post_with_no_length_and_no_body_is_not_too_large():
    call = _guard({"a": "token-a"})
    assert call("token-a", [b""]) == 200
    assert call("token-a", [b"x" * 10, b"y" * 10]) == 413, "a chunked body past the bound"
    assert call("token-a", [b"{}"], [(b"content-length", b"999")]) == 413


def test_the_rate_limit_counts_each_caller_on_its_own():
    call = _guard({"a": "token-a", "b": "token-b"}, max_requests_per_minute=2)
    empty = [(b"content-length", b"0")]
    assert [call("token-a", [b""], empty) for _ in range(3)] == [200, 200, 429]
    assert call("token-b", [b""], empty) == 200, "another caller is not slowed by the first one"
