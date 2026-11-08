"""Operator-fixable failures end in one line naming what to fix, never in a traceback.

Covers configuration files for all four commands, observation files given to the planner, and
the collector's recognition of each driver's login, missing-database and unreachable-server
errors. The live counterpart against real servers is `test_live_operator_errors.py`; these tests
pin the mapping itself with each driver's real exception types, including that the driver's own
text, which can carry host names, user names and passwords, is never repeated.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner
from dbig_builder.builder import assemble
from dbig_builder.cli import main as builder_main
from dbig_builder.config import BuilderConfig
from dbig_collectors.cli import main as collector_main
from dbig_collectors.config import SourceConfig
from dbig_collectors.runtime import _connection_failure
from dbig_contracts.errors import OperatorError
from dbig_planner.cli import main as planner_main
from dbig_requester.cli import main as requester_main
from test_builder import source_bundle

SECRET = "leaky-secret-in-driver-text"  # noqa: S105 # C5: reason=asserted absent from every message; approved=user, 2026-11-08; review=2027-05-08


def _source_config(kind: str = "postgresql") -> SourceConfig:
    return SourceConfig(
        source_id="site-a",
        kind=kind,
        connection_env="SITE_A_CONNECTION",
        signing_key_env="SITE_A_KEY",
        signing_key_id="k",
        namespaces=["public"],
    )


def _chained(outer: BaseException, inner: BaseException) -> BaseException:
    try:
        try:
            raise inner
        except BaseException as error:
            raise outer from error
    except BaseException as error:
        return error


def _driver(module: str, name: str) -> type[BaseException]:
    """A driver's own exception type. The drivers are optional extras of the collector, absent
    from the application-quality job, so those cases skip there and run in source-integration,
    which installs every extra."""
    m = pytest.importorskip(module)  # C5: reason=dep; approved=user, 2026-11-08; review=2027-05-08
    return getattr(m, name)


def _pg_error(code: str) -> BaseException:
    return _driver("pg8000.exceptions", "DatabaseError")(
        {"S": "FATAL", "C": code, "M": f"user {SECRET} said no"}
    )


@pytest.mark.parametrize(
    ("kind", "error", "expected"),
    [
        ("postgresql", lambda: _pg_error("28P01"), "the server refused the login"),
        ("postgresql", lambda: _pg_error("28000"), "the server refused the login"),
        ("postgresql", lambda: _pg_error("3D000"), "the database named in SITE_A_CONNECTION"),
        (
            "postgresql",
            lambda: _chained(
                _driver("pg8000.exceptions", "InterfaceError")(
                    f"Can't create a connection to host {SECRET}"
                ),
                ConnectionRefusedError(111, "Connection refused"),
            ),
            "the connection was refused",
        ),
        (
            "postgresql",
            lambda: _chained(RuntimeError(SECRET), socket.gaierror(-2, "Name not known")),
            "the host name was not found",
        ),
        (
            "clickhouse",
            lambda: _driver("clickhouse_connect.driver.exceptions", "DatabaseError")(
                f"Received ClickHouse exception, code: 516, server response: {SECRET}"
            ),
            "the server refused the login",
        ),
        (
            "clickhouse",
            lambda: _chained(
                _driver("clickhouse_connect.driver.exceptions", "OperationalError")(
                    f"Error HTTPSConnectionPool({SECRET})"
                ),
                TimeoutError("timed out"),
            ),
            "the server did not answer in time",
        ),
        (
            "mongodb",
            lambda: _driver("pymongo.errors", "OperationFailure")(
                f"Authentication failed for {SECRET}", 18
            ),
            "the server refused the login",
        ),
        (
            "mongodb",
            lambda: _driver("pymongo.errors", "ServerSelectionTimeoutError")(
                f"{SECRET}:27017: [Errno 111] Connection refused"
            ),
            "no server answered in time",
        ),
    ],
)
def test_driver_failures_become_one_line_without_driver_text(kind, error, expected):
    converted = _connection_failure(error(), _source_config(kind))
    assert isinstance(converted, OperatorError)
    message = str(converted)
    assert expected in message
    assert SECRET not in message
    assert message.startswith(f"{kind} source 'site-a': ")


@pytest.mark.parametrize(
    "error",
    [
        lambda: _pg_error("42501"),  # permission denied: not a login problem
        lambda: _driver("pymongo.errors", "OperationFailure")("not authorized", 13),
        lambda: ValueError("a genuine bug"),
    ],
)
def test_other_failures_are_left_alone(error):
    assert _connection_failure(error(), _source_config()) is None


def _invoke(main, args):
    return CliRunner().invoke(main, args)


def _assert_operator_error(result, *expected: str) -> None:
    assert result.exit_code != 0
    assert not isinstance(result.exception, Exception) or isinstance(
        result.exception, SystemExit
    ), result.exception
    for text in expected:
        assert text in result.output, result.output


CONFIG_COMMANDS = [
    (collector_main, ["collect", "--out", "unused"], "source"),
    (builder_main, ["assemble", "--bundle", ".", "--out", "unused"], "builder"),
    (requester_main, ["index"], "requester"),
    (planner_main, ["serve", "--host", "127.0.0.1", "--port", "1"], "planner"),
]


@pytest.mark.parametrize(("main", "args", "section"), CONFIG_COMMANDS)
def test_a_config_that_is_not_toml_is_a_command_error(tmp_path, main, args, section):
    config = tmp_path / "bad.toml"
    config.write_text(f"[{section}]\nsource_id = {SECRET}\n", encoding="utf-8")
    result = _invoke(main, [*args[:1], "--config", str(config), *args[1:]])
    _assert_operator_error(result, "is not valid TOML", "line 2")
    assert SECRET not in result.output


@pytest.mark.parametrize(("main", "args", "section"), CONFIG_COMMANDS)
def test_a_config_with_the_wrong_table_is_a_command_error(tmp_path, main, args, section):
    config = tmp_path / "bad.toml"
    config.write_text("[somewhere_else]\nkey = 1\n", encoding="utf-8")
    result = _invoke(main, [*args[:1], "--config", str(config), *args[1:]])
    _assert_operator_error(result, f"must contain only a [{section}] table")


def test_an_invalid_setting_is_named_without_its_value(tmp_path):
    config = tmp_path / "source.toml"
    config.write_text(
        "\n".join(
            [
                "[source]",
                'source_id = "site-a"',
                'kind = "postgresql"',
                'connection_env = "SITE_A_CONNECTION"',
                'signing_key_env = "SITE_A_KEY"',
                'signing_key_id = "k"',
                'namespaces = ["public"]',
                f'max_queriez = "{SECRET}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = _invoke(collector_main, ["collect", "--config", str(config), "--out", "unused"])
    _assert_operator_error(result, "is invalid", "max_queriez")
    assert SECRET not in result.output


def test_a_malformed_environment_override_is_a_command_error(tmp_path, monkeypatch):
    config = tmp_path / "source.toml"
    config.write_text(
        '[source]\nsource_id = "s"\nkind = "postgresql"\nconnection_env = "C"\n'
        'signing_key_env = "K"\nsigning_key_id = "k"\nnamespaces = ["public"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DBIG_MAX_QUERIES", "eight hundred")
    result = _invoke(collector_main, ["collect", "--config", str(config), "--out", "unused"])
    _assert_operator_error(result, "DBIG_MAX_QUERIES is not a valid value")


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source_bundle(tmp_path / "bundle")
    monkeypatch.setenv("BUNDLE_KEY", "synthetic-key-12345678901234567890")
    assemble(
        [tmp_path / "bundle"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    return tmp_path / "snapshot"


def _build(snapshot: Path, observations: Path, out: Path):
    return _invoke(
        planner_main,
        [
            "build", "--snapshot", str(snapshot), "--observations", str(observations),
            "--out", str(out),
        ],
    )


def test_an_invalid_observation_names_its_file_and_line_not_its_sql(tmp_path, snapshot):
    observations = tmp_path / "observations"
    observations.mkdir()
    good = {
        "kind": "pipeline", "id": "p1", "input_object_ids": ["object:a"],
        "output_object_id": "object:b", "observed_at": "2026-10-01T10:00:00Z",
        "status": "executed",
    }
    missing_id = {
        "kind": "sql", "source_id": "site-a", "dialect": "postgres",
        "sql": f"SELECT '{SECRET}'", "observed_at": "2026-10-01T10:00:00Z",
        "status": "executed",
    }
    (observations / "log.jsonl").write_text(
        json.dumps(good) + "\n" + json.dumps(missing_id) + "\n", encoding="utf-8"
    )
    result = _build(snapshot, observations, tmp_path / "graph")
    _assert_operator_error(result, "observation log.jsonl:2 is invalid", "id: Field required")
    assert SECRET not in result.output
    assert not (tmp_path / "graph").exists()


def test_a_line_that_is_not_json_names_its_file_and_line(tmp_path, snapshot):
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "log.jsonl").write_text(f"{{not json {SECRET}\n", encoding="utf-8")
    result = _build(snapshot, observations, tmp_path / "graph")
    _assert_operator_error(result, "observation log.jsonl:1 is not a valid JSON record")
    assert SECRET not in result.output


def test_a_corrupt_snapshot_record_keeps_its_traceback(tmp_path, snapshot):
    """Only observation records are converted; a snapshot record failing validation is not
    something the operator wrote, so it stays a traceback (TECHNICAL_IMPLEMENTATION §12a)."""
    from dbig_planner import graph

    def broken(*args, **kwargs):
        from dbig_contracts.models import Object

        Object.model_validate({})

    (tmp_path / "empty").mkdir()
    original = graph._build_graph
    graph._build_graph = broken
    try:
        result = _build(snapshot, tmp_path / "empty", tmp_path / "graph")
    finally:
        graph._build_graph = original
    assert result.exit_code != 0
    assert type(result.exception).__name__ == "ValidationError"


def test_an_invalid_dataset_mapping_is_a_command_error(tmp_path, snapshot):
    (tmp_path / "empty").mkdir()
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"dataset_to_object_idz": {SECRET: "object:a"}}), "utf-8")
    result = _invoke(
        planner_main,
        [
            "build", "--snapshot", str(snapshot), "--observations", str(tmp_path / "empty"),
            "--out", str(tmp_path / "graph"), "--mapping", str(mapping),
        ],
    )
    _assert_operator_error(result, f"dataset mapping {mapping} is invalid")
    assert SECRET not in result.output


def test_a_signing_key_under_32_bytes_is_a_one_line_error_naming_the_variable(
    tmp_path, monkeypatch
):
    """E1: a short DBIG_SIGNING_KEY was a ValueError traceback, now a command error."""
    config = tmp_path / "source.toml"
    config.write_text(
        '[source]\nsource_id = "s"\nkind = "postgresql"\nconnection_env = "C"\n'
        'signing_key_env = "SHORT_KEY"\nsigning_key_id = "k"\nnamespaces = ["public"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("C", "{}")
    monkeypatch.setenv("SHORT_KEY", "too-short-to-sign")
    result = _invoke(collector_main, ["collect", "--config", str(config), "--out", "unused"])
    _assert_operator_error(result, "SHORT_KEY")
    assert "at least 32 bytes" in result.output
    assert "too-short-to-sign" not in result.output
