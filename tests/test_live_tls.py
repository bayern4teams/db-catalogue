"""`collect` against all three real engines, over verified TLS and over loopback plaintext.

`test_live_pipeline.py` covers PostgreSQL over TLS, trusting the CA through `SSL_CERT_FILE`. This
file covers what an operator with servers on a private network actually runs: every engine over
verified TLS with the CA given as `tls_ca_file`, a certificate from an untrusted CA refused by
every engine, the loopback-only plaintext opt-out, and one catalogue assembled from all three.

Opt-in, like the other live tests: skipped unless `DBIG_RUN_INTEGRATION=1`, and then it needs
`DBIG_TEST_CA_FILE`, the throwaway CA the fixture servers' certificates are issued by. The
servers must listen with TLS on PostgreSQL 5432, ClickHouse 8443 and MongoDB 27017, and in
plaintext on PostgreSQL 5432, ClickHouse 8123 and MongoDB 27017 (MongoDB in `preferTLS` mode).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DBIG_RUN_INTEGRATION") != "1", reason="live source containers unavailable"
)

SCRIPTS = Path(sys.executable).parent
NAMESPACE = "dbig_tls"
SIGNING_KEY = "synthetic-tls-signing-key-well-over-32-bytes"
CONNECTIONS = {
    "postgresql": {"host": "127.0.0.1", "port": 5432, "database": "postgres", "user": "postgres"},
    "clickhouse": {"host": "127.0.0.1", "port": 8443},
    "mongodb": {"uri": "mongodb://127.0.0.1:27017"},
}
PLAINTEXT = {**CONNECTIONS, "clickhouse": {"host": "127.0.0.1", "port": 8123}}
# Fixture DDL on a constant namespace; the collectors themselves bind every value.
POSTGRES_SEED = (
    f"DROP SCHEMA IF EXISTS {NAMESPACE} CASCADE",
    f"CREATE SCHEMA {NAMESPACE}",
    f"CREATE TABLE {NAMESPACE}.customers (id integer PRIMARY KEY, email text)",  # noqa: S608
    f"INSERT INTO {NAMESPACE}.customers VALUES (1, 'a'), (2, 'b'), (3, NULL)",  # noqa: S608
    f"ANALYZE {NAMESPACE}.customers",
)


def _ca_file() -> Path:
    value = os.environ.get("DBIG_TEST_CA_FILE")
    if not value:
        pytest.fail("DBIG_TEST_CA_FILE must name the fixture CA when DBIG_RUN_INTEGRATION=1")
    return Path(value)


def _retry(action, seconds: int = 120):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return action()
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


@pytest.fixture(scope="module", autouse=True)
def seeded() -> None:
    """One small schema per engine, over plaintext loopback so seeding does not depend on the
    TLS path under test."""
    import clickhouse_connect
    from pg8000 import dbapi
    from pymongo import MongoClient

    connection = _retry(
        lambda: dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    )
    try:
        cursor = connection.cursor()
        for statement in POSTGRES_SEED:
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()
    client = _retry(lambda: clickhouse_connect.get_client(host="127.0.0.1", port=8123))
    try:
        client.command(f"DROP DATABASE IF EXISTS {NAMESPACE}")
        client.command(f"CREATE DATABASE {NAMESPACE} ENGINE=Atomic")
        client.command(
            f"CREATE TABLE {NAMESPACE}.events (id UInt64, customer_id Int32, day Date) "
            "ENGINE=MergeTree ORDER BY id"
        )
    finally:
        client.close()
    mongo = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
    _retry(lambda: mongo.admin.command("ping"))
    try:
        mongo.drop_database(NAMESPACE)
        orders = mongo[NAMESPACE].create_collection(
            "orders",
            validator={"$jsonSchema": {"properties": {"customer_id": {"bsonType": "int"}}}},
        )
        orders.create_index("customer_id")
    finally:
        mongo.close()


def _collect(
    tmp_path: Path, kind: str, *, tls: bool = True, ca: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    executable = SCRIPTS / "dbig"
    if not executable.exists():
        pytest.fail("the collector's `dbig` console script is not installed")
    lines = [
        "[source]",
        f'source_id = "tls-{kind}"',
        f'kind = "{kind}"',
        'connection_env = "DBIG_CONNECTION"',
        'signing_key_env = "DBIG_SIGNING_KEY"',
        'signing_key_id = "ci"',
        f'namespaces = ["{NAMESPACE}"]',
        f"tls = {'true' if tls else 'false'}",
    ]
    if ca is not None:
        lines.append(f'tls_ca_file = "{ca.as_posix()}"')
    config = tmp_path / f"{kind}.toml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"SSL_CERT_FILE", "SSL_CERT_DIR"}
    }
    env.update(
        {
            "DBIG_CONNECTION": json.dumps((CONNECTIONS if tls else PLAINTEXT)[kind]),
            "DBIG_SIGNING_KEY": SIGNING_KEY,
            **(extra_env or {}),
        }
    )
    bundle = tmp_path / f"bundle-{kind}"
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, paths from tmp_path
        [str(executable), "collect", "--config", str(config), "--out", str(bundle)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return result, bundle


def _source(bundle: Path) -> dict:
    from dbig_contracts.artifact import read_artifact

    _, data = read_artifact(bundle, signing_key=SIGNING_KEY.encode())
    return data["sources.jsonl"][0].model_dump()


ENGINES = ["postgresql", "clickhouse", "mongodb"]


@pytest.mark.parametrize("kind", ENGINES)
def test_collect_over_verified_tls_with_a_private_ca(tmp_path, kind):
    """`SSL_CERT_FILE` is removed from the environment, so only `tls_ca_file` can make the
    certificate verify: a bundle here proves the option reaches this engine's driver."""
    result, bundle = _collect(tmp_path, kind, ca=_ca_file())
    assert result.returncode == 0, result.stderr
    assert _source(bundle)["transport"] == "tls-verified"


@pytest.mark.parametrize("kind", ENGINES)
def test_an_untrusted_certificate_is_refused(tmp_path, kind):
    """The same server, but its CA is trusted by nothing the collector was given. Verification
    must fail and nothing may be published; this is what proves the TLS above was verified
    rather than merely encrypted."""
    result, bundle = _collect(tmp_path, kind, ca=None)
    assert result.returncode != 0
    assert not (bundle / "manifest.json").exists()
    # And the operator is told what to fix, in one line rather than a driver traceback.
    assert "TLS certificate did not verify" in result.stderr
    assert "tls_ca_file" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("kind", ENGINES)
def test_plaintext_is_allowed_on_loopback_and_recorded(tmp_path, kind):
    result, bundle = _collect(tmp_path, kind, tls=False)
    assert result.returncode == 0, result.stderr
    assert _source(bundle)["transport"] == "plaintext-loopback"


def test_one_catalogue_from_all_three_engines(tmp_path):
    """The operator's actual run: three sources collected over verified TLS, assembled into one
    snapshot and rendered into one page, with a relationship proposed across engines."""
    bundles = []
    for kind in ENGINES:
        result, bundle = _collect(tmp_path, kind, ca=_ca_file())
        assert result.returncode == 0, f"{kind}: {result.stderr}"
        bundles.append(bundle)
    (tmp_path / "builder.toml").write_text(
        '[builder]\nsigning_keys = { ci = "DBIG_SIGNING_KEY" }\n', encoding="utf-8"
    )
    env = {**os.environ, "DBIG_SIGNING_KEY": SIGNING_KEY}
    bundle_args = [argument for bundle in bundles for argument in ("--bundle", str(bundle))]
    assembled = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPTS / "dbig-builder"), "assemble", "--config", str(tmp_path / "builder.toml"),
         *bundle_args, "--out", str(tmp_path / "snapshot")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert assembled.returncode == 0, assembled.stderr
    rendered = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPTS / "dbig-builder"), "render", "--snapshot", str(tmp_path / "snapshot"),
         *bundle_args, "--config", str(tmp_path / "builder.toml"),
         "--out", str(tmp_path / "catalog.html")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert rendered.returncode == 0, rendered.stderr
    counts = json.loads(rendered.stdout)
    assert counts["sources"] == 3
    page = (tmp_path / "catalog.html").read_text(encoding="utf-8")
    for expected in ("tls-postgresql", "tls-clickhouse", "tls-mongodb", "customers", "events",
                     "orders", "tls-verified", "Completeness: <b>complete</b>"):
        assert expected in page, expected
    assert counts["candidates"] >= 1, "customer_id in ClickHouse and MongoDB should point at " \
        "the PostgreSQL customers key"
    assert SIGNING_KEY not in page
