"""What an operator sees when `collect` cannot log in, cannot reach a server, or is pointed at a
database or namespace that is not there, against the three real engines.

Each case must end in one line naming the setting to fix, with no traceback and without
repeating the password. A namespace that does not exist must stop the run: before this check a
misspelt namespace produced an empty bundle marked complete. An existing but empty namespace is
still a valid, empty scan.

Opt-in like the other live tests (`DBIG_RUN_INTEGRATION=1` and `DBIG_TEST_CA_FILE`), against the
servers `ci.yml` starts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DBIG_RUN_INTEGRATION") != "1", reason="live source containers unavailable"
)

SCRIPTS = Path(sys.executable).parent
SIGNING_KEY = "synthetic-operator-error-signing-key-over-32-bytes"
USER = "dbig_error_probe"
PASSWORD = "probe-password-that-must-not-leak"  # noqa: S105 - throwaway fixture account
WRONG = "wrong-password-that-must-not-leak"
EXISTING = "dbig_errors"
EMPTY = "dbig_errors_empty"
CLOSED_PORT = 1  # nothing listens on port 1 on the runner


def _ca_file() -> Path:
    value = os.environ.get("DBIG_TEST_CA_FILE")
    if not value:
        pytest.fail("DBIG_TEST_CA_FILE must name the fixture CA when DBIG_RUN_INTEGRATION=1")
    return Path(value)


@pytest.fixture(scope="module", autouse=True)
def seeded() -> None:
    """A login with a known password on ClickHouse and MongoDB, and one populated and one empty
    namespace on each engine (MongoDB has no empty database, so only a populated one)."""
    import clickhouse_connect
    from pg8000 import dbapi
    from pymongo import MongoClient

    connection = dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    try:
        cursor = connection.cursor()
        for statement in (
            f"DROP SCHEMA IF EXISTS {EXISTING} CASCADE",
            f"DROP SCHEMA IF EXISTS {EMPTY} CASCADE",
            f"CREATE SCHEMA {EXISTING}",
            f"CREATE SCHEMA {EMPTY}",
            f"CREATE TABLE {EXISTING}.things (id integer PRIMARY KEY)",
        ):
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123)
    try:
        for statement in (
            f"DROP DATABASE IF EXISTS {EXISTING}",
            f"DROP DATABASE IF EXISTS {EMPTY}",
            f"CREATE DATABASE {EXISTING}",
            f"CREATE DATABASE {EMPTY}",
            f"CREATE TABLE {EXISTING}.things (id UInt64) ENGINE=MergeTree ORDER BY id",
            f"DROP USER IF EXISTS {USER}",
            f"CREATE USER {USER} IDENTIFIED WITH sha256_password BY '{PASSWORD}' "
            "SETTINGS readonly = 2",
            f"GRANT SHOW TABLES, SHOW COLUMNS ON {EXISTING}.* TO {USER}",
            f"GRANT SHOW TABLES, SHOW COLUMNS ON {EMPTY}.* TO {USER}",
        ):
            client.command(statement)
    finally:
        client.close()
    mongo = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
    try:
        mongo.drop_database(EXISTING)
        mongo[EXISTING].create_collection("things")
        if mongo.admin.command("usersInfo", USER)["users"]:
            mongo.admin.command("dropUser", USER)
        mongo.admin.command(
            "createUser", USER, pwd=PASSWORD, roles=[{"role": "read", "db": EXISTING}]
        )
    finally:
        mongo.close()


def _collect(
    tmp_path: Path, kind: str, connection: dict, namespace: str = EXISTING
) -> subprocess.CompletedProcess[str]:
    config = tmp_path / f"{kind}.toml"
    config.write_text(
        "\n".join(
            [
                "[source]",
                f'source_id = "errors-{kind}"',
                f'kind = "{kind}"',
                'connection_env = "DBIG_CONNECTION"',
                'signing_key_env = "DBIG_SIGNING_KEY"',
                'signing_key_id = "ci"',
                f'namespaces = ["{namespace}"]',
                f'tls_ca_file = "{_ca_file().as_posix()}"',
                "read_timeout_seconds = 5",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"SSL_CERT_FILE", "SSL_CERT_DIR"}
    }
    env.update({"DBIG_CONNECTION": json.dumps(connection), "DBIG_SIGNING_KEY": SIGNING_KEY})
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, paths from tmp_path
        [
            str(SCRIPTS / "dbig"), "collect", "--config", str(config),
            "--out", str(tmp_path / f"bundle-{kind}"),
        ],
        capture_output=True, text=True, env=env, timeout=120,
    )


def _refused(result: subprocess.CompletedProcess[str], expected: str) -> str:
    assert result.returncode != 0, result.stdout
    assert "Traceback" not in result.stderr, result.stderr
    assert expected in result.stderr, result.stderr
    assert PASSWORD not in result.stderr and WRONG not in result.stderr
    assert "DBIG_CONNECTION" in result.stderr or "namespaces" in result.stderr
    return result.stderr


def _pg(**overrides) -> dict:
    return {
        "host": "127.0.0.1", "port": 5432, "database": "postgres", "user": "postgres",
        **overrides,
    }


def _ch(**overrides) -> dict:
    return {"host": "127.0.0.1", "port": 8443, "username": USER, "password": PASSWORD, **overrides}


def _mongo(password: str = PASSWORD, port: int = 27017) -> dict:
    return {"uri": f"mongodb://{USER}:{password}@127.0.0.1:{port}/?authSource=admin"}


@pytest.mark.parametrize(
    ("kind", "connection"),
    [
        # The CI PostgreSQL trusts every login, so an unknown role is the refusal it can show;
        # SQLSTATE 28000 takes the same path as a wrong password (28P01).
        ("postgresql", _pg(user="no_such_role")),
        ("clickhouse", _ch(password=WRONG)),
        ("mongodb", _mongo(password=WRONG)),
    ],
)
def test_a_refused_login_names_the_connection_setting(tmp_path, kind, connection):
    _refused(_collect(tmp_path, kind, connection), "the server refused the login")
    assert not (tmp_path / f"bundle-{kind}").exists()


def test_an_unknown_postgresql_database_is_named_as_such(tmp_path):
    _refused(
        _collect(tmp_path, "postgresql", _pg(database="no_such_database")),
        "the database named in DBIG_CONNECTION does not exist",
    )


@pytest.mark.parametrize(
    ("kind", "connection"),
    [
        ("postgresql", _pg(port=CLOSED_PORT)),
        ("clickhouse", _ch(port=CLOSED_PORT)),
        ("mongodb", _mongo(port=CLOSED_PORT)),
    ],
)
def test_an_unreachable_server_names_host_and_port(tmp_path, kind, connection):
    _refused(_collect(tmp_path, kind, connection), "could not reach the server")


@pytest.mark.parametrize(
    ("kind", "connection"),
    [("postgresql", _pg()), ("clickhouse", _ch()), ("mongodb", _mongo())],
)
def test_a_misspelt_namespace_stops_without_a_bundle(tmp_path, kind, connection):
    result = _collect(tmp_path, kind, connection, namespace="dbig_errorz")
    _refused(result, "'dbig_errorz' listed in namespaces does not exist")
    assert not (tmp_path / f"bundle-{kind}").exists()


@pytest.mark.parametrize(
    ("kind", "connection", "namespace"),
    [
        ("postgresql", _pg(), EMPTY),
        ("clickhouse", _ch(), EMPTY),
        ("postgresql", _pg(), EXISTING),
        ("clickhouse", _ch(), EXISTING),
        ("mongodb", _mongo(), EXISTING),
    ],
)
def test_an_existing_namespace_still_collects(tmp_path, kind, connection, namespace):
    result = _collect(tmp_path, kind, connection, namespace=namespace)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / f"bundle-{kind}" / "manifest.json").is_file()
