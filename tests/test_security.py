"""Contract tests for the shared secret-reference resolver.

Every component reads credentials and signing keys indirectly, by the *name* of an environment
variable held in validated configuration. `secret_from_env` is the single place that resolves
such a reference, so it must fail closed on every way a reference can be wrong, name the variable
so an operator can fix it, and never put the secret's value in the error.
"""

from __future__ import annotations

import pytest
from dbig_contracts.security import secret_from_env

SYNTHETIC_VALUE = "synthetic-signing-key-1234567890"


def test_a_set_variable_resolves_to_its_exact_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBIG_TEST_SECRET", SYNTHETIC_VALUE)
    assert secret_from_env("DBIG_TEST_SECRET", purpose="test") == SYNTHETIC_VALUE


def test_the_value_is_returned_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signing keys are byte-sensitive, so the resolver must not trim or normalise a real value."""
    padded = f" {SYNTHETIC_VALUE}\n"
    monkeypatch.setenv("DBIG_TEST_SECRET", padded)
    assert secret_from_env("DBIG_TEST_SECRET", purpose="test") == padded


def test_an_unset_variable_is_refused_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DBIG_ABSENT_SECRET", raising=False)
    with pytest.raises(ValueError, match="DBIG_ABSENT_SECRET, which is not set"):
        secret_from_env("DBIG_ABSENT_SECRET", purpose="bundle signature verification")


def test_an_empty_variable_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DBIG_TEST_SECRET", "")
    with pytest.raises(ValueError, match="DBIG_TEST_SECRET for test is empty"):
        secret_from_env("DBIG_TEST_SECRET", purpose="test")


def test_a_whitespace_only_variable_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A variable set to spaces must not be accepted as a signing key or bearer token."""
    monkeypatch.setenv("DBIG_TEST_SECRET", "   \t\n")
    with pytest.raises(ValueError, match="is empty"):
        secret_from_env("DBIG_TEST_SECRET", purpose="test")


@pytest.mark.parametrize("reference", ["", "   ", None])
def test_a_blank_reference_is_refused(reference: str | None) -> None:
    """Configuration that names no variable must stop the caller, not read a variable named ''."""
    with pytest.raises(ValueError, match="requires a non-empty environment variable reference"):
        secret_from_env(reference, purpose="scan request signing")


def test_the_purpose_is_reported_so_the_operator_knows_which_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DBIG_ABSENT_SECRET", raising=False)
    with pytest.raises(ValueError, match="scan request signature verification"):
        secret_from_env("DBIG_ABSENT_SECRET", purpose="scan request signature verification")


def test_a_refusal_never_contains_the_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank-looking but non-empty value must still not be echoed into the error text."""
    monkeypatch.setenv("DBIG_TEST_SECRET", " ")
    with pytest.raises(ValueError, match="is empty") as failure:
        secret_from_env("DBIG_TEST_SECRET", purpose="test")
    assert " " not in str(failure.value)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True), ("[::1]", True),
        (" 127.0.0.1 ", True), ("localhost", False), ("0.0.0.0", False), ("10.0.0.5", False),  # noqa: S104
        ("::", False), ("[::]", False), ("", False), ("example.com", False), ("[127.0.0.1", False),
    ],
)
def test_one_loopback_rule_for_every_boundary(host, expected):
    """The rule behind plaintext database connections, plain-HTTP model endpoints and `serve`
    binds. It lived in three copies that had already drifted: only one accepted `[::1]`, and the
    `serve` copy raised on a hostname instead of refusing it."""
    from dbig_contracts.security import is_loopback_literal

    assert is_loopback_literal(host) is expected
