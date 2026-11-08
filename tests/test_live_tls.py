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

pytestmark = pytest.mark.skipif(  # C5: reason=opt-in; approved=user, 2026-11-08; review=2027-05-08
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
    f"CREATE TABLE {NAMESPACE}.customers (id integer PRIMARY KEY, email text)",
    f"INSERT INTO {NAMESPACE}.customers VALUES (1, 'a'), (2, 'b'), (3, NULL)",  # noqa: S608 # C5: reason=fixture SQL built from constant names; approved=user, 2026-11-08; review=2027-05-08
    f"COMMENT ON TABLE {NAMESPACE}.customers IS 'Everyone who has bought something'",
    f"COMMENT ON COLUMN {NAMESPACE}.customers.email IS 'Contact address'",
    # A declared chain inside PostgreSQL, so a path from another engine runs through several
    # PostgreSQL tables in a row and the planner must join them in one query.
    f"CREATE TABLE {NAMESPACE}.invoices (id integer PRIMARY KEY, "
    f"customer_id integer REFERENCES {NAMESPACE}.customers(id))",
    f"INSERT INTO {NAMESPACE}.invoices VALUES (10, 1), (11, 2)",  # noqa: S608 # C5: reason=fixture SQL built from constant names; approved=user, 2026-11-08; review=2027-05-08
    f"CREATE TABLE {NAMESPACE}.invoice_lines (id integer PRIMARY KEY, "
    f"invoice_id integer REFERENCES {NAMESPACE}.invoices(id))",
    f"INSERT INTO {NAMESPACE}.invoice_lines VALUES (100, 10), (101, 10), (102, 11)",  # noqa: S608 # C5: reason=fixture SQL built from constant names; approved=user, 2026-11-08; review=2027-05-08
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
            "ENGINE=MergeTree ORDER BY id COMMENT 'Product events'"
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
        # Orders for customers 1 and 2, so the planned PostgreSQL read has keys to filter by.
        orders.insert_many([{"customer_id": 1}, {"customer_id": 2}])
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
    result = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell, paths from tmp_path; approved=user, 2026-11-08; review=2027-05-08
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
    assembled = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        [str(SCRIPTS / "dbig-builder"), "assemble", "--config", str(tmp_path / "builder.toml"),
         *bundle_args, "--out", str(tmp_path / "snapshot")],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert assembled.returncode == 0, assembled.stderr
    rendered = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
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
    for comment in ("Everyone who has bought something", "Contact address", "Product events"):
        assert comment in page, f"comment missing from the page: {comment}"

    # Search, as an operator would: a prefix finds the table, and a question finds its column.
    (tmp_path / "requester.toml").write_text(
        f'[requester]\nsnapshot_dir = "{(tmp_path / "snapshot").as_posix()}"\n'
        f'state_dir = "{(tmp_path / "state").as_posix()}"\napi_key_env = "REQUESTER_TOKEN"\n',
        encoding="utf-8",
    )
    _tool(env, "dbig-requester", "index", "--config", str(tmp_path / "requester.toml"))
    found = _tool(env, "dbig-requester", "search", "--config", str(tmp_path / "requester.toml"),
                  "--query", "customer")
    ids = {row["name"]: row["id"] for row in found["results"] if row["kind"] != "field"}
    assert "customers" in ids, "a prefix must find the table"
    asked = _tool(env, "dbig-requester", "search", "--config", str(tmp_path / "requester.toml"),
                  "--query", "which column has the customer contact address", "--match", "any")
    assert asked["results"][0]["name"] == "email", "the comment must make the column findable"

    # A multi-hop path across engines, planned, and the PostgreSQL part executed read-only.
    mongo_orders = next(
        row["id"] for row in _tool(
            env, "dbig-requester", "search", "--config", str(tmp_path / "requester.toml"),
            "--query", "orders", "--source-id", "tls-mongodb",
        )["results"] if row["kind"] == "collection"
    )
    lines = next(
        row["id"] for row in _tool(
            env, "dbig-requester", "search", "--config", str(tmp_path / "requester.toml"),
            "--query", "invoice_lines",
        )["results"] if row["name"] == "invoice_lines"
    )
    (tmp_path / "observations").mkdir()
    _tool(env, "dbig-planner", "build", "--snapshot", str(tmp_path / "snapshot"),
          "--observations", str(tmp_path / "observations"), "--out", str(tmp_path / "graph"))
    paths = _tool(env, "dbig-planner", "join-paths", "--graph", str(tmp_path / "graph"),
                  "--from", mongo_orders, "--to", lines)
    assert paths["paths"], "orders (MongoDB) -> customers -> invoices -> invoice_lines"
    plan = _tool(env, "dbig-planner", "plan", "--graph", str(tmp_path / "graph"),
                 "--path-id", paths["paths"][0]["path_id"])
    assert plan["status"] == "unresolved", "a cross-engine join needs a staging target"
    assert [step["kind"] for step in plan["steps"]] == [
        "source_read", "source_read", "logical_join",
    ], "the three PostgreSQL tables must be one joined read"
    sql = plan["steps"][1]["sql"]
    assert sql.count(" JOIN ") == 2
    # The PostgreSQL read is restricted to the customers the MongoDB read returned, as a bound
    # parameter; `test_live_plans.py` executes every kind of step, this is the operator's path.
    (parameter,) = plan["steps"][1]["parameters"]
    assert parameter["values_from_step"] == 0
    from pymongo import MongoClient

    mongo = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
    try:
        orders = list(
            mongo[NAMESPACE][plan["steps"][0]["collection"]].aggregate(
                plan["steps"][0]["pipeline"]
            )
        )
    finally:
        mongo.close()
    keys = sorted({row[parameter["values_from_column"]] for row in orders})
    assert keys == [1, 2]

    from pg8000 import dbapi

    connection = dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    style, dbapi.paramstyle = dbapi.paramstyle, "pyformat"
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(sql, {parameter["name"]: keys})
        assert len(cursor.fetchall()) == 3, "every invoice line joins to its invoice and customer"
    finally:
        dbapi.paramstyle = style
        connection.rollback()
        connection.close()


def _tool(env: dict[str, str], name: str, *arguments: str) -> dict:
    executable = SCRIPTS / name
    if not executable.exists():
        pytest.fail(f"the `{name}` console script is not installed")
    result = subprocess.run(  # noqa: S603 # C5: reason=fixed argv, no shell; approved=user, 2026-11-08; review=2027-05-08
        [str(executable), *arguments], capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, f"{name} {arguments[0]}: {result.stderr}"
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
