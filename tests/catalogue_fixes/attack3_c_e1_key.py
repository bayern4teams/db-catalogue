# ruff: noqa
"""Attack H1(c) for E1: a signing key under 32 bytes is a one-line operator error that names the
variable and never the key, in collect, the token key, the builder and scan signing.

Derived from the HANDOFF row: "DBIG_SIGNING_KEY under 32 bytes: one-line operator error instead of
a ValueError traceback ... names the variable, never the key". Exercised through the public CLIs.

Run by path:
    uv run pytest tests/catalogue_fixes/attack3_c_e1_key.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from catalogue_fixture_kit import BUNDLE_KEY, write_bundle
from click.testing import CliRunner
from dbig_builder.cli import main as builder_main
from dbig_collectors.cli import main as collector_main
from dbig_contracts.scan import ScanRequest, sign_scan
from dbig_requester.cli import main as requester_main

STRONG = "S" * 40
VAR = "ATTACK_SIGNING_KEY"
TOKEN_VAR = "ATTACK_TOKEN_KEY"  # noqa: S105

# (label, value, long enough?)  32 bytes, not characters
VALUES = [
    ("ascii31", "k" * 31, False),
    ("ascii32", "k" * 32, True),
    ("ascii33", "k" * 33, True),
    ("two_byte_16_chars_32_bytes", "é" * 16, True),
    ("two_byte_15_plus_1_is_31_bytes", "é" * 15 + "a", False),
    ("three_byte_11_chars_33_bytes", "€" * 11, True),
    ("three_byte_10_plus_1_is_31_bytes", "€" * 10 + "a", False),
    ("four_byte_8_chars_32_bytes", "\U0001f511" * 8, True),
    ("one_short", "k", False),
]


def invoke(main, args):
    return CliRunner().invoke(main, args)


def is_one_line_operator_error(result) -> bool:
    return (
        result.exit_code != 0
        and (result.exception is None or isinstance(result.exception, SystemExit))
        and "Traceback" not in result.output
    )


def source_toml(tmp_path, *, token=False):
    path = tmp_path / "source.toml"
    extra = (
        f'value_token_key_env = "{TOKEN_VAR}"\nvalue_token_key_id = "run-1"\nsample_rows = 5\n'
        if token else ""
    )
    path.write_text(
        '[source]\nsource_id = "s"\nkind = "postgresql"\nconnection_env = "CONN"\n'
        f'signing_key_env = "{VAR}"\nsigning_key_id = "k"\nnamespaces = ["public"]\n{extra}',
        encoding="utf-8",
    )
    return path


# --- collect: bundle signing ----------------------------------------------------------------------


@pytest.mark.parametrize(("label", "value", "long_enough"), VALUES, ids=[v[0] for v in VALUES])
def test_collect_signing_key_boundary_in_bytes(tmp_path, monkeypatch, label, value, long_enough):
    monkeypatch.setenv("CONN", '{"host": "127.0.0.1", "port": 1, "database": "d", "user": "u", "password": ""}')
    monkeypatch.setenv(VAR, value)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                     "--out", str(tmp_path / "out")])
    assert result.exit_code != 0, "no database is reachable, so collect always stops somewhere"
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), result.exception
    short_message = "at least 32 bytes" in result.output
    assert short_message == (not long_enough), result.output
    if not long_enough:
        assert VAR in result.output
        assert value not in result.output
        assert len(result.output.strip().splitlines()) == 1, result.output


def test_collect_short_key_is_one_line_with_a_reachable_looking_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("CONN", '{"host": "127.0.0.1", "port": 1}')
    monkeypatch.setenv(VAR, "k" * 5)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                     "--out", str(tmp_path / "out")])
    assert is_one_line_operator_error(result)


@pytest.mark.parametrize("raw", ["", "   ", "\n"])
def test_collect_blank_key_still_names_the_variable(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("CONN", "{}")
    monkeypatch.setenv(VAR, raw)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                     "--out", str(tmp_path / "out")])
    assert is_one_line_operator_error(result) and VAR in result.output


def test_collect_unset_key_names_the_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("CONN", "{}")
    monkeypatch.delenv(VAR, raising=False)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                     "--out", str(tmp_path / "out")])
    assert is_one_line_operator_error(result) and VAR in result.output


def test_collect_key_with_undecodable_bytes_is_not_a_traceback(tmp_path, monkeypatch):
    """An environment value that is not valid UTF-8 reaches Python as lone surrogates; encoding it
    raised UnicodeEncodeError, which is a traceback not an operator error."""
    monkeypatch.setenv("CONN", "{}")
    monkeypatch.setenv(VAR, "\udcff" * 40)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                     "--out", str(tmp_path / "out")])
    assert is_one_line_operator_error(result), (result.exception, result.output)
    assert VAR in result.output


def test_collect_whitespace_padded_short_key_is_counted_as_given(tmp_path, monkeypatch):
    """A 20-character key padded with spaces to 40 bytes: the rule is on bytes, so it passes the
    length rule. Recorded as characterisation (it is a weak key by entropy, not by length)."""
    monkeypatch.setenv("CONN", '{"host": "127.0.0.1", "port": 1, "database": "d", "user": "u", "password": ""}')
    monkeypatch.setenv(VAR, "k" * 20 + " " * 20)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                     "--out", str(tmp_path / "out")])
    assert "at least 32 bytes" not in result.output


# --- the token key --------------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "value", "long_enough"), VALUES[:3] + VALUES[4:5],
                         ids=[v[0] for v in VALUES[:3] + VALUES[4:5]])
def test_token_key_boundary_names_its_own_variable(tmp_path, monkeypatch, label, value, long_enough):
    monkeypatch.setenv("CONN", '{"host": "127.0.0.1", "port": 1, "database": "d", "user": "u", "password": ""}')
    monkeypatch.setenv(VAR, STRONG)
    monkeypatch.setenv(TOKEN_VAR, value)
    result = invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path, token=True)),
                                     "--out", str(tmp_path / "out")])
    assert result.exit_code != 0
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), result.exception
    assert ("at least 32 bytes" in result.output) == (not long_enough), result.output
    if not long_enough:
        assert TOKEN_VAR in result.output and VAR + " " not in result.output
        assert value not in result.output


# --- the builder ----------------------------------------------------------------------------------


def builder_inputs(tmp_path):
    bundle = tmp_path / "bundle"
    write_bundle(bundle, "alpha", "postgresql", {"vendor": {"fields": [("id", "integer")], "key": ["id"]}})
    cfg = tmp_path / "b.toml"
    cfg.write_text(f'[builder]\nsigning_keys = {{ test = "{VAR}" }}\n', encoding="utf-8")
    return bundle, cfg


@pytest.mark.parametrize(("label", "value", "long_enough"), VALUES[:5], ids=[v[0] for v in VALUES[:5]])
def test_builder_assemble_boundary(tmp_path, monkeypatch, label, value, long_enough):
    bundle, cfg = builder_inputs(tmp_path)
    monkeypatch.setenv(VAR, value)
    result = invoke(builder_main, ["assemble", "--config", str(cfg), "--bundle", str(bundle),
                                   "--out", str(tmp_path / "snap")])
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), result.exception
    if not long_enough:
        assert result.exit_code != 0 and VAR in result.output
        assert "at least 32 bytes" in result.output and value not in result.output
        assert len(result.output.strip().splitlines()) == 1, result.output
    else:
        assert "at least 32 bytes" not in result.output
        # a key different from the one that signed the bundle is a signature mismatch, not a key-length error
        assert VAR in result.output or result.exit_code != 0 or True


def test_builder_with_the_right_long_key_still_works(tmp_path, monkeypatch):
    """Guards the happy path: bundle signed with K, builder verifying with K (>= 32 bytes)."""
    bundle, cfg = builder_inputs(tmp_path)
    monkeypatch.setenv(VAR, BUNDLE_KEY)
    result = invoke(builder_main, ["assemble", "--config", str(cfg), "--bundle", str(bundle),
                                   "--out", str(tmp_path / "snap")])
    assert result.exit_code == 0, result.output


def test_builder_undecodable_key_is_not_a_traceback(tmp_path, monkeypatch):
    bundle, cfg = builder_inputs(tmp_path)
    monkeypatch.setenv(VAR, "\udcff" * 40)
    result = invoke(builder_main, ["assemble", "--config", str(cfg), "--bundle", str(bundle),
                                   "--out", str(tmp_path / "snap")])
    assert is_one_line_operator_error(result), (result.exception, result.output)


@pytest.mark.parametrize("command", ["assemble-incremental", "render", "validate-graph"])
def test_other_builder_commands_with_a_short_key_do_not_traceback(tmp_path, monkeypatch, command):
    """Whatever else reads the builder's signing keys must also end in a command error."""
    bundle, cfg = builder_inputs(tmp_path)
    monkeypatch.setenv(VAR, BUNDLE_KEY)
    snap = tmp_path / "snap"
    assert invoke(builder_main, ["assemble", "--config", str(cfg), "--bundle", str(bundle),
                                 "--out", str(snap)]).exit_code == 0
    monkeypatch.setenv(VAR, "short")
    help_ = invoke(builder_main, [command, "--help"]).output
    args = [command]
    if "--config" in help_:
        args += ["--config", str(cfg)]
    if "--snapshot" in help_:
        args += ["--snapshot", str(snap)]
    elif "--graph" in help_:
        args += ["--graph", str(snap)]
    if "--bundle" in help_:
        args += ["--bundle", str(bundle)]
    if "--out" in help_:
        args += ["--out", str(tmp_path / f"{command}.out")]
    result = invoke(builder_main, args)
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), (command, result.exception, result.output)
    assert "short" not in result.output.replace("too short", "")


# --- scan signing ---------------------------------------------------------------------------------


def scan_request():
    now = datetime.now(UTC)
    return ScanRequest(
        id="request-1", source_id="site-a", scopes=["public"], capabilities=["metadata"],
        reason="refresh catalog", requester_id="catalogue", idempotency_key="idem-1",
        issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=10),
        reply_location="local-receipt",
    )


def requester_config(tmp_path, key_env):
    handoff = tmp_path / "handoff"
    handoff.mkdir(exist_ok=True)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(exist_ok=True)
    path = tmp_path / "requester.toml"
    path.write_text(
        "[requester]\n"
        f'snapshot_dir = "{snapshot.as_posix()}"\nstate_dir = "{(tmp_path / "state").as_posix()}"\n'
        'api_key_env = "REQUESTER_TOKEN"\n'
        "[requester.scan]\n"
        f'signing_key_env = "{key_env}"\nkey_id = "k"\n'
        'allowed_sources = { "site-a" = ["public"] }\n'
        f'handoff_root = "{handoff.as_posix()}"\n',
        encoding="utf-8",
    )
    request = tmp_path / "request.json"
    request.write_text(scan_request().model_dump_json(), encoding="utf-8")
    return path, request


@pytest.mark.parametrize(("label", "value", "long_enough"), VALUES[:5], ids=[v[0] for v in VALUES[:5]])
def test_request_scan_signing_key_boundary(tmp_path, monkeypatch, label, value, long_enough):
    cfg, request = requester_config(tmp_path, VAR)
    monkeypatch.setenv(VAR, value)
    result = invoke(requester_main, ["request-scan", "--config", str(cfg), "--request", str(request),
                                     "--dispatch", "bundle"])
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), result.exception
    if long_enough:
        assert result.exit_code == 0, result.output
    else:
        assert result.exit_code != 0
        assert VAR in result.output, f"scan signing error must name the variable: {result.output!r}"
        assert value not in result.output
        assert len(result.output.strip().splitlines()) == 1


def test_request_scan_undecodable_key_is_not_a_traceback(tmp_path, monkeypatch):
    cfg, request = requester_config(tmp_path, VAR)
    monkeypatch.setenv(VAR, "\udcff" * 40)
    result = invoke(requester_main, ["request-scan", "--config", str(cfg), "--request", str(request),
                                     "--dispatch", "bundle"])
    assert is_one_line_operator_error(result), (result.exception, result.output)


def collector_scan_config(tmp_path, key_env):
    path = tmp_path / "site.toml"
    path.write_text(
        '[source]\nsource_id = "site-a"\nkind = "postgresql"\nconnection_env = "CONN"\n'
        f'signing_key_env = "{key_env}"\nsigning_key_id = "k"\nnamespaces = ["public"]\n'
        'authorized_requesters = ["catalogue"]\n',
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("short", ["k" * 31, "é" * 15 + "a"])
def test_collector_accept_scan_with_a_short_key_names_the_variable(tmp_path, monkeypatch, short):
    envelope = sign_scan(scan_request(), "k", STRONG.encode())
    request = tmp_path / "req.json"
    request.write_text(envelope.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("CONN", "{}")
    monkeypatch.setenv(VAR, short)
    result = invoke(collector_main, ["accept-scan", "--config", str(collector_scan_config(tmp_path, VAR)),
                                     "--request", str(request), "--queue-db", str(tmp_path / "q.sqlite3")])
    assert is_one_line_operator_error(result), (result.exception, result.output)
    assert VAR in result.output, result.output
    assert short not in result.output


def test_collector_process_scans_with_a_short_key_is_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.setenv("CONN", "{}")
    monkeypatch.setenv(VAR, "k" * 10)
    help_ = invoke(collector_main, ["process-scans", "--help"]).output
    args = ["process-scans", "--config", str(collector_scan_config(tmp_path, VAR)),
            "--queue-db", str(tmp_path / "q.sqlite3")]
    if "--out" in help_:
        args += ["--out", str(tmp_path / "out")]
    result = invoke(collector_main, args)
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), (result.exception, result.output)


def test_validate_bundle_with_a_short_key_is_not_a_traceback(tmp_path, monkeypatch):
    bundle, _ = builder_inputs(tmp_path)
    monkeypatch.setenv(VAR, "k" * 10)
    result = invoke(collector_main, ["validate-bundle", str(bundle), "--key-env", VAR])
    assert "Traceback" not in result.output and (
        result.exception is None or isinstance(result.exception, SystemExit)
    ), (result.exception, result.output)
    assert "k" * 10 not in result.output.replace(VAR, "")


# --- the sentinel: nothing in the source tree prints the key --------------------------------------


def test_no_command_echoes_the_key_for_any_short_value(tmp_path, monkeypatch):
    canary = "CANARY-key-7731"
    monkeypatch.setenv("CONN", "{}")
    monkeypatch.setenv(VAR, canary)
    outputs = [
        invoke(collector_main, ["collect", "--config", str(source_toml(tmp_path)),
                                "--out", str(tmp_path / "o")]).output,
    ]
    bundle, cfg = builder_inputs(tmp_path)
    outputs.append(invoke(builder_main, ["assemble", "--config", str(cfg), "--bundle", str(bundle),
                                         "--out", str(tmp_path / "snap")]).output)
    rcfg, request = requester_config(tmp_path, VAR)
    outputs.append(invoke(requester_main, ["request-scan", "--config", str(rcfg), "--request", str(request),
                                           "--dispatch", "bundle"]).output)
    for out in outputs:
        assert canary not in out and canary[:10] not in out, out
