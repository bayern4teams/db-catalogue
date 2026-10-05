"""Plans executed on the three real engines, exactly as a reviewer would run them, and compared
with the true answer computed from the full tables.

Each `source_read` runs on its own server with its parameters bound from the named column of the
step it cites: PostgreSQL through pg8000's `pyformat` placeholders bound to a list, ClickHouse
through its server-side query parameters, MongoDB through the aggregate command's `let`. The
`logical_join` is then done here, as a staging target would. Two properties are checked:

- every column of a joined read has its own name (`SELECT *` returned one `id` per table);
- a cross-source plan finds every match for the rows its first read returned. Before key
  filtering, each read took an independent sample, and the tickets here point at customers
  beyond the first sample, so that plan joined to nothing.

Opt-in like the other live tests (`DBIG_RUN_INTEGRATION=1` and `DBIG_TEST_CA_FILE`).
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
SIGNING_KEY = "synthetic-plan-signing-key-well-over-32-bytes"
NAMESPACE = "dbig_plans"
CUSTOMERS = 200
# Small enough that an unfiltered read of `customers` cannot contain the customers the tickets
# point at, so only a key-filtered read can find them.
MAX_ROWS = 20
TICKET_CUSTOMERS = [150 + offset * 7 for offset in range(8)]  # 150, 157, ..., 199
CONNECTIONS = {
    "postgresql": {"host": "127.0.0.1", "port": 5432, "database": "postgres", "user": "postgres"},
    "clickhouse": {"host": "127.0.0.1", "port": 8443},
    "mongodb": {"uri": "mongodb://127.0.0.1:27017"},
}


def _ca_file() -> Path:
    value = os.environ.get("DBIG_TEST_CA_FILE")
    if not value:
        pytest.fail("DBIG_TEST_CA_FILE must name the fixture CA when DBIG_RUN_INTEGRATION=1")
    return Path(value)


def _pg():
    from pg8000 import dbapi

    return dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")


def _ch():
    import clickhouse_connect

    return clickhouse_connect.get_client(host="127.0.0.1", port=8123)


def _mongo():
    from pymongo import MongoClient

    return MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)


@pytest.fixture(scope="module")
def catalogue(tmp_path_factory) -> dict:
    """customers <- orders <- refunds in PostgreSQL, page_views in ClickHouse and tickets in
    MongoDB pointing at customers, collected, assembled and built into a planner graph."""
    connection = _pg()
    try:
        cursor = connection.cursor()
        for statement in (
            f"DROP SCHEMA IF EXISTS {NAMESPACE} CASCADE",
            f"CREATE SCHEMA {NAMESPACE}",
            f"CREATE TABLE {NAMESPACE}.customers (id integer PRIMARY KEY, email text)",
            f"CREATE TABLE {NAMESPACE}.orders (id integer PRIMARY KEY, "
            f"customer_id integer REFERENCES {NAMESPACE}.customers(id), total integer)",
            f"CREATE TABLE {NAMESPACE}.refunds (id integer PRIMARY KEY, "
            f"order_id integer REFERENCES {NAMESPACE}.orders(id), amount integer)",
            f"INSERT INTO {NAMESPACE}.customers SELECT g, 'c' || g "  # noqa: S608
            f"FROM generate_series(1, {CUSTOMERS}) g",
            # Two orders per customer, and a refund on every third order.
            f"INSERT INTO {NAMESPACE}.orders SELECT g, 1 + (g - 1) / 2, g "  # noqa: S608
            f"FROM generate_series(1, {CUSTOMERS * 2}) g",
            f"INSERT INTO {NAMESPACE}.refunds SELECT g, g * 3, 1 "  # noqa: S608
            f"FROM generate_series(1, {CUSTOMERS * 2 // 3}) g",
            f"ANALYZE {NAMESPACE}.customers",
        ):
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()
    client = _ch()
    try:
        client.command(f"DROP DATABASE IF EXISTS {NAMESPACE}")
        client.command(f"CREATE DATABASE {NAMESPACE}")
        client.command(
            f"CREATE TABLE {NAMESPACE}.page_views (id UInt64, customer_id Int32) "
            "ENGINE=MergeTree ORDER BY id"
        )
        client.command(
            f"INSERT INTO {NAMESPACE}.page_views SELECT number, "  # noqa: S608
            "toInt32(150 + number % 10) FROM numbers(30)"
        )
    finally:
        client.close()
    mongo = _mongo()
    try:
        mongo.drop_database(NAMESPACE)
        mongo[NAMESPACE]["tickets"].insert_many(
            [{"_id": index, "customer_id": value} for index, value in enumerate(TICKET_CUSTOMERS)]
        )
        mongo[NAMESPACE]["tickets"].create_index("customer_id")
    finally:
        mongo.close()

    root = tmp_path_factory.mktemp("plans")
    env = {key: value for key, value in os.environ.items() if key != "SSL_CERT_FILE"}
    env["DBIG_SIGNING_KEY"] = SIGNING_KEY
    bundles = []
    for kind, connection_value in CONNECTIONS.items():
        config = root / f"{kind}.toml"
        config.write_text(
            f'[source]\nsource_id = "plans-{kind}"\nkind = "{kind}"\n'
            'connection_env = "DBIG_CONNECTION"\nsigning_key_env = "DBIG_SIGNING_KEY"\n'
            f'signing_key_id = "ci"\nnamespaces = ["{NAMESPACE}"]\n'
            f'tls_ca_file = "{_ca_file().as_posix()}"\n'
            + ("sample_rows = 20\n" if kind == "mongodb" else ""),
            encoding="utf-8",
        )
        bundle = root / f"bundle-{kind}"
        _tool(
            {**env, "DBIG_CONNECTION": json.dumps(connection_value)},
            "dbig", "collect", "--config", str(config), "--out", str(bundle),
        )
        bundles += ["--bundle", str(bundle)]
    (root / "builder.toml").write_text(
        '[builder]\nsigning_keys = { ci = "DBIG_SIGNING_KEY" }\n', encoding="utf-8"
    )
    _tool(env, "dbig-builder", "assemble", "--config", str(root / "builder.toml"), *bundles,
          "--out", str(root / "snapshot"))
    (root / "observations").mkdir()
    _tool(env, "dbig-planner", "build", "--snapshot", str(root / "snapshot"),
          "--observations", str(root / "observations"), "--out", str(root / "graph"))
    from dbig_contracts.artifact import read_artifact

    _, tables = read_artifact(root / "snapshot")
    ids = {
        (item.source_id, item.name): item.id
        for item in tables["nodes.jsonl"]
        if getattr(item, "record_type", None) == "object"
    }
    return {"env": env, "graph": root / "graph", "ids": ids, "root": root}


def _tool(env: dict[str, str], name: str, *arguments: str) -> dict:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPTS / name), *arguments], capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, f"{name} {arguments[0]}: {result.stderr}"
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}


def _plan(catalogue: dict, start: tuple[str, str], end: tuple[str, str]) -> dict:
    ids = catalogue["ids"]
    paths = _tool(
        catalogue["env"], "dbig-planner", "join-paths", "--graph", str(catalogue["graph"]),
        "--from", ids[start], "--to", ids[end],
    )
    assert paths["paths"], f"no path from {start} to {end}"
    shortest = min(paths["paths"], key=lambda item: len(item["object_ids"]))
    from dbig_planner.config import PlannerConfig
    from dbig_planner.plans import PlanRequest, render_plan

    return render_plan(
        catalogue["graph"],
        PlanRequest(path_id=shortest["path_id"], max_rows=MAX_ROWS),
        PlannerConfig(graph_dir=catalogue["graph"]),
    )


def _bind(step: dict, results: list[list[dict]]) -> dict[str, list]:
    values = {}
    for parameter in step["parameters"]:
        rows = results[parameter["values_from_step"]]
        values[parameter["name"]] = sorted(
            {row[parameter["values_from_column"]] for row in rows}
            - {None}
        )
    return values


def _run_read(step: dict, results: list[list[dict]]) -> list[dict]:
    """Run one `source_read` or `sql_read` read-only, binding its parameters as documented."""
    values = _bind(step, results)
    if "sql" in step and step["dialect"] == "postgres":
        from pg8000 import dbapi

        connection = _pg()
        # pg8000 reads its placeholder style from a module setting, not from the cursor.
        style, dbapi.paramstyle = dbapi.paramstyle, "pyformat"
        try:
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION READ ONLY")
            if values:
                cursor.execute(step["sql"], values)
            else:
                cursor.execute(step["sql"])
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        finally:
            dbapi.paramstyle = style
            connection.rollback()
            connection.close()
    if "sql" in step:
        client = _ch()
        try:
            result = client.query(step["sql"], parameters=values)
            return [dict(zip(result.column_names, row, strict=True)) for row in result.result_set]
        finally:
            client.close()
    mongo = _mongo()
    try:
        cursor = mongo[NAMESPACE][step["collection"]].aggregate(
            step["pipeline"], let=values or None
        )
        return list(cursor)
    finally:
        mongo.close()


def _execute(plan: dict) -> list[list[dict]]:
    results: list[list[dict]] = []
    for step in plan["steps"]:
        if step["kind"] != "logical_join":
            results.append(_run_read(step, results))
    return results


def test_a_joined_read_names_every_column_once(catalogue):
    plan = _plan(
        catalogue, ("plans-postgresql", "refunds"), ("plans-postgresql", "customers")
    )
    assert plan["status"] == "ready_for_review"
    assert "*" not in plan["steps"][0]["sql"]
    rows = _execute(plan)[0]
    assert len(rows) == MAX_ROWS
    names = list(rows[0])
    assert len(names) == len(set(names)), names
    assert {"refunds__id", "orders__id", "customers__id", "customers__email"} <= set(names)
    for row in rows:
        assert row["refunds__order_id"] == row["orders__id"]
        assert row["orders__customer_id"] == row["customers__id"]


def test_mongodb_to_postgresql_finds_every_match_beyond_the_first_sample(catalogue):
    """tickets (MongoDB) -> customers -> orders -> refunds (PostgreSQL)."""
    plan = _plan(catalogue, ("plans-mongodb", "tickets"), ("plans-postgresql", "refunds"))
    assert plan["status"] == "unresolved"
    first, second, join = plan["steps"]
    assert (first["kind"], second["kind"], join["kind"]) == (
        "source_read", "source_read", "logical_join",
    )
    assert second["parameters"][0]["values_from_column"] == "customer_id"
    tickets, joined = _execute(plan)
    assert sorted(row["customer_id"] for row in tickets) == TICKET_CUSTOMERS
    # Every refund reachable from those customers, which an unfiltered first-20 read of
    # customers (ids 1..20) could never contain.
    expected = {
        refund
        for refund in range(1, CUSTOMERS * 2 // 3 + 1)
        if 1 + (refund * 3 - 1) // 2 in TICKET_CUSTOMERS
    }
    assert {row["refunds__id"] for row in joined} == expected
    key = second["parameters"][0]["filters_field"]
    right = next(name for name in joined[0] if name.endswith(f"__{key}"))
    matched = [
        (ticket["_id"], row["refunds__id"])
        for ticket in tickets for row in joined if row[right] == ticket["customer_id"]
    ]
    assert {refund for _, refund in matched} == expected


def test_postgresql_to_mongodb_binds_keys_through_let(catalogue):
    """customers (PostgreSQL) -> tickets (MongoDB): the MongoDB read is restricted by `let`."""
    plan = _plan(catalogue, ("plans-postgresql", "customers"), ("plans-mongodb", "tickets"))
    first, second, _ = plan["steps"]
    assert second["parameter_binding"] == "aggregate let"
    customers, tickets = _execute(plan)
    sampled = {row["customers__id"] for row in customers}
    assert len(sampled) == MAX_ROWS
    assert {row["customer_id"] for row in tickets} == sampled & set(TICKET_CUSTOMERS)


def test_clickhouse_reads_bind_server_side_parameters(catalogue):
    """page_views (ClickHouse) -> customers (PostgreSQL), and back."""
    plan = _plan(
        catalogue, ("plans-clickhouse", "page_views"), ("plans-postgresql", "customers")
    )
    views, customers = _execute(plan)
    viewed = {row["page_views__customer_id"] for row in views}
    assert viewed and viewed <= set(range(150, 160))
    assert {row["customers__id"] for row in customers} == viewed

    back = _plan(
        catalogue, ("plans-postgresql", "customers"), ("plans-clickhouse", "page_views")
    )
    assert back["steps"][1]["parameter_binding"] == "server-side query parameters"
    assert "{keys_1_1: Array(Int32)}" in back["steps"][1]["sql"]
    customers, views = _execute(back)
    # The first 20 customers have no page views; key filtering returns none rather than an
    # unrelated sample that a later join would silently drop.
    assert {row["customers__id"] for row in customers} <= set(range(1, CUSTOMERS + 1))
    assert {row["page_views__customer_id"] for row in views} == (
        {row["customers__id"] for row in customers} & set(range(150, 160))
    )


def test_the_run_plan_helper_reproduces_the_true_cross_source_join(catalogue):
    """`scripts/run_plan.py` is what the live-test guide gives an operator to check a plan on
    their own databases; it must bind and join exactly as the plan describes."""
    root = catalogue["root"]
    env = dict(catalogue["env"])
    configs = []
    for kind, connection in CONNECTIONS.items():
        variable = f"RUN_PLAN_{kind.upper()}"
        env[variable] = json.dumps(connection)
        path = root / f"run-plan-{kind}.toml"
        path.write_text(
            f'[source]\nsource_id = "plans-{kind}"\nkind = "{kind}"\n'
            f'connection_env = "{variable}"\nsigning_key_env = "DBIG_SIGNING_KEY"\n'
            f'signing_key_id = "ci"\nnamespaces = ["{NAMESPACE}"]\n'
            f'tls_ca_file = "{_ca_file().as_posix()}"\n',
            encoding="utf-8",
        )
        configs += ["--config", str(path)]
    plan = _plan(catalogue, ("plans-mongodb", "tickets"), ("plans-postgresql", "refunds"))
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_plan.py"
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(script), "--plan", str(root / "plan.json"), *configs],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    tickets, joined = report["steps"]
    assert tickets["rows"] == len(TICKET_CUSTOMERS)
    assert joined["bound_keys"] == {"keys_1_1": len(TICKET_CUSTOMERS)}
    expected = sum(
        1 for refund in range(1, CUSTOMERS * 2 // 3 + 1)
        for customer in TICKET_CUSTOMERS
        if 1 + (refund * 3 - 1) // 2 == customer
    )
    (join,) = report["joins"]
    refunded = {1 + (refund * 3 - 1) // 2 for refund in range(1, CUSTOMERS * 2 // 3 + 1)}
    # A ticket whose customer has no refund has no row in the inner-joined PostgreSQL read.
    assert join["joined_rows"] == expected
    assert join["left_rows_without_a_match"] == len(set(TICKET_CUSTOMERS) - refunded)
    assert "sample" not in tickets, "no row values unless asked for"
