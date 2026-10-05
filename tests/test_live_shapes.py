"""Schemas shaped like real ones, on the three real engines: what CI's small fixtures never had.

PostgreSQL: two schemas, a mixed-case schema, a table name with a space, column names needing
quotes (`AccountID`, `e-mail`), a composite primary and foreign key, a cross-schema foreign key,
a range-partitioned table with partitions, a view and a materialized view. ClickHouse: a table
name with a space, `Nullable`, `LowCardinality`, `Array`, `Map` and `DateTime64` columns, a
materialized view writing to another table, a view and a `ReplacingMergeTree`. MongoDB: nested
documents, arrays of documents, nested arrays, a field holding two types, a mostly-null field,
a view and a capped collection.

Three defects were found by building this, each pinned here:

- MongoDB `sample_rows` was a total for the whole source, so after the first collection with
  that many documents every later one was left unsampled and had no fields at all;
- MongoDB's internal `system.views` was catalogued as data, publishing the shape of every view's
  pipeline;
- type families compared ClickHouse `String`, PostgreSQL `text` and MongoDB `str` as different
  types, and `Nullable(...)` or `LowCardinality(...)` as types of their own, so no text key and no
  nullable ClickHouse key could be proposed across engines.

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
SIGNING_KEY = "synthetic-shapes-signing-key-well-over-32-bytes"
POSTGRES_SEED = (
    'DROP SCHEMA IF EXISTS "Sales" CASCADE',
    "DROP SCHEMA IF EXISTS ref CASCADE",
    'CREATE SCHEMA "Sales"',
    "CREATE SCHEMA ref",
    "CREATE TABLE ref.regions (code text PRIMARY KEY, name text)",
    'CREATE TABLE "Sales"."Customer Accounts" ("AccountID" integer PRIMARY KEY, '
    '"Region" text REFERENCES ref.regions(code), "e-mail" text)',
    'COMMENT ON COLUMN "Sales"."Customer Accounts"."e-mail" IS \'Billing contact\'',
    'CREATE TABLE "Sales".order_headers (tenant_id integer, order_no integer, '
    '"AccountID" integer REFERENCES "Sales"."Customer Accounts"("AccountID"), '
    "PRIMARY KEY (tenant_id, order_no))",
    'CREATE TABLE "Sales".order_lines (tenant_id integer, order_no integer, line_no integer, '
    "sku text, PRIMARY KEY (tenant_id, order_no, line_no), FOREIGN KEY (tenant_id, order_no) "
    'REFERENCES "Sales".order_headers (tenant_id, order_no))',
    'CREATE TABLE "Sales".events (id bigint, happened date NOT NULL, "AccountID" integer) '
    "PARTITION BY RANGE (happened)",
    'CREATE TABLE "Sales".events_2026q3 PARTITION OF "Sales".events '
    "FOR VALUES FROM ('2026-07-01') TO ('2026-10-01')",
    'CREATE TABLE "Sales".events_2026q4 PARTITION OF "Sales".events '
    "FOR VALUES FROM ('2026-10-01') TO ('2027-01-01')",
    'CREATE VIEW "Sales".active_accounts AS SELECT "AccountID", "Region" '
    'FROM "Sales"."Customer Accounts" WHERE "e-mail" IS NOT NULL',
    'CREATE MATERIALIZED VIEW "Sales".lines_per_order AS SELECT tenant_id, order_no, '
    'count(*) AS n FROM "Sales".order_lines GROUP BY 1, 2',
    "INSERT INTO ref.regions VALUES ('EU', 'Europe'), ('US', 'United States')",
    'INSERT INTO "Sales"."Customer Accounts" SELECT g, CASE WHEN g % 2 = 0 THEN \'EU\' '
    "ELSE 'US' END, 'a' || g || '@x' FROM generate_series(1, 50) g",
    'INSERT INTO "Sales".order_headers SELECT t, o, 1 + (o % 50) '
    "FROM generate_series(1, 3) t, generate_series(1, 40) o",
    'INSERT INTO "Sales".order_lines SELECT t, o, l, \'sku\' || l '
    "FROM generate_series(1, 3) t, generate_series(1, 40) o, generate_series(1, 2) l",
    'INSERT INTO "Sales".events SELECT g, DATE \'2026-07-15\' + (g % 120), 1 + (g % 50) '
    "FROM generate_series(1, 300) g",
    'REFRESH MATERIALIZED VIEW "Sales".lines_per_order',
    "ANALYZE",
)
CLICKHOUSE_SEED = (
    "DROP DATABASE IF EXISTS shapes",
    "CREATE DATABASE shapes",
    "CREATE TABLE shapes.`Page Views` (id UInt64, `AccountID` Nullable(Int32), "
    "path LowCardinality(String), region_code LowCardinality(String), tags Array(String), "
    "props Map(String, String), at DateTime64(3)) ENGINE = MergeTree ORDER BY id "
    "COMMENT 'Raw views'",
    "CREATE TABLE shapes.daily (day Date, `AccountID` Int32, views UInt64) "
    "ENGINE = SummingMergeTree ORDER BY (day, `AccountID`)",
    "CREATE MATERIALIZED VIEW shapes.daily_mv TO shapes.daily AS SELECT toDate(at) AS day, "
    "assumeNotNull(`AccountID`) AS `AccountID`, count() AS views FROM shapes.`Page Views` "
    "GROUP BY day, `AccountID`",
    "CREATE VIEW shapes.recent AS SELECT id, `AccountID` FROM shapes.`Page Views` "
    "WHERE at > now() - INTERVAL 1 DAY",
    "CREATE TABLE shapes.dedup (id UInt64, ver UInt32, `AccountID` Int32) "
    "ENGINE = ReplacingMergeTree(ver) ORDER BY id",
    "INSERT INTO shapes.`Page Views` SELECT number, "
    "if(number % 10 = 0, NULL, toInt32(1 + number % 50)), 'p', "
    "if(number % 2 = 0, 'EU', 'US'), ['a'], map('k', 'v'), now64(3) FROM numbers(500)",
)


def _ca_file() -> Path:
    value = os.environ.get("DBIG_TEST_CA_FILE")
    if not value:
        pytest.fail("DBIG_TEST_CA_FILE must name the fixture CA when DBIG_RUN_INTEGRATION=1")
    return Path(value)


def _tool(env: dict[str, str], name: str, *arguments: str) -> dict:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [str(SCRIPTS / name), *arguments], capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, f"{name} {arguments[0]}: {result.stderr}"
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}


@pytest.fixture(scope="module")
def catalogue(tmp_path_factory) -> dict:
    import clickhouse_connect
    from dbig_contracts.artifact import read_artifact
    from pg8000 import dbapi
    from pymongo import MongoClient

    connection = dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    try:
        cursor = connection.cursor()
        for statement in POSTGRES_SEED:
            cursor.execute(statement)
        connection.commit()
    finally:
        connection.close()
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123)
    try:
        for statement in CLICKHOUSE_SEED:
            client.command(statement)
    finally:
        client.close()
    mongo = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
    try:
        mongo.drop_database("shapes")
        database = mongo["shapes"]
        # More documents than sample_rows, so a source-wide row total would starve the rest.
        database["tickets"].insert_many(
            [
                {
                    "_id": index + 1,
                    "account": {"id": index % 50 + 1, "region": "US" if index % 2 else "EU"},
                    "items": [{"sku": "sku1", "qty": index % 3}, {"sku": "sku2"}],
                    "status": "open" if index % 3 else 1,
                    "notes": None if index % 5 else "x",
                    "history": [[1, 2], [3]],
                }
                for index in range(60)
            ]
        )
        database.command("create", "open_tickets", viewOn="tickets",
                         pipeline=[{"$match": {"status": "open"}}])
        database.create_collection("capped_log", capped=True, size=100_000)
        # Also larger than sample_rows: whichever of the two collections is listed second
        # would get no sample under a source-wide total, whatever order the server lists them.
        database["capped_log"].insert_many(
            [{"AccountID": index % 50 + 1, "msg": "m"} for index in range(60)]
        )
    finally:
        mongo.close()

    root = tmp_path_factory.mktemp("shapes")
    env = {key: value for key, value in os.environ.items() if key != "SSL_CERT_FILE"}
    env["DBIG_SIGNING_KEY"] = SIGNING_KEY
    sources = {
        "postgresql": ('["Sales", "ref"]', {"host": "127.0.0.1", "port": 5432,
                                           "database": "postgres", "user": "postgres"}),
        "clickhouse": ('["shapes"]', {"host": "127.0.0.1", "port": 8443}),
        "mongodb": ('["shapes"]', {"uri": "mongodb://127.0.0.1:27017"}),
    }
    bundles = []
    for kind, (namespaces, connection_value) in sources.items():
        config = root / f"{kind}.toml"
        config.write_text(
            f'[source]\nsource_id = "shapes-{kind}"\nkind = "{kind}"\n'
            'connection_env = "DBIG_CONNECTION"\nsigning_key_env = "DBIG_SIGNING_KEY"\n'
            f'signing_key_id = "ci"\nnamespaces = {namespaces}\n'
            f'tls_ca_file = "{_ca_file().as_posix()}"\n'
            + ("sample_rows = 50\n" if kind == "mongodb" else ""),
            encoding="utf-8",
        )
        _tool({**env, "DBIG_CONNECTION": json.dumps(connection_value)}, "dbig", "collect",
              "--config", str(config), "--out", str(root / f"bundle-{kind}"))
        bundles += ["--bundle", str(root / f"bundle-{kind}")]
    (root / "builder.toml").write_text(
        '[builder]\nsigning_keys = { ci = "DBIG_SIGNING_KEY" }\n', encoding="utf-8"
    )
    _tool(env, "dbig-builder", "assemble", "--config", str(root / "builder.toml"), *bundles,
          "--out", str(root / "snapshot"))
    _tool(env, "dbig-builder", "render", "--config", str(root / "builder.toml"),
          "--snapshot", str(root / "snapshot"), *bundles, "--out", str(root / "page.html"))
    (root / "observations").mkdir()
    # A join an application runs on ClickHouse, with no key or candidate behind it.
    (root / "observations" / "queries.jsonl").write_text(
        json.dumps({  # noqa: S608 - fixed fixture SQL, written to a file, never executed here
            "kind": "sql", "id": "ch-join", "source_id": "shapes-clickhouse",
            "dialect": "clickhouse", "status": "executed", "observed_at": "2026-10-01T10:00:00Z",
            "sql": "SELECT v.id, d.ver FROM shapes.`Page Views` AS v "
                   "JOIN shapes.dedup AS d ON v.id = d.id",
        }) + "\n",
        encoding="utf-8",
    )
    _tool(env, "dbig-planner", "build", "--snapshot", str(root / "snapshot"),
          "--observations", str(root / "observations"), "--out", str(root / "graph"))
    _, tables = read_artifact(root / "snapshot")
    nodes = tables["nodes.jsonl"]
    objects = {item.id: item for item in nodes if item.record_type == "object"}
    snapshot_manifest, _ = read_artifact(root / "snapshot")
    mongo_manifest, mongo_bundle = read_artifact(
        root / "bundle-mongodb", signing_key=SIGNING_KEY.encode()
    )
    return {
        "snapshot_completeness": snapshot_manifest.completeness,
        "mongo_completeness": mongo_manifest.completeness,
        "root": root,
        "objects": objects,
        "by_name": {(item.source_id, item.name): item for item in objects.values()},
        "fields": [item for item in nodes if item.record_type == "field"],
        "edges": tables["edges.jsonl"],
        "mongo_diagnostics": [item.model_dump() for item in mongo_bundle["diagnostics.jsonl"]],
        "page": (root / "page.html").read_text(encoding="utf-8"),
    }


def _fields(catalogue: dict, source: str, name: str) -> dict[str, object]:
    owner = catalogue["by_name"][(source, name)].id
    return {item.name: item for item in catalogue["fields"] if item.object_id == owner}


def _pairs(catalogue: dict, edge) -> list[tuple[str, str]]:
    names = {item.id: item.name for item in catalogue["fields"]}
    return [(names[a], names[b]) for a, b in (edge.model_extra or {}).get("field_pairs", [])]


def test_postgresql_object_kinds_partitions_and_quoted_names(catalogue):
    by_name = catalogue["by_name"]
    kinds = {
        name: by_name[("shapes-postgresql", name)].kind
        for name in ("Customer Accounts", "events", "events_2026q3", "active_accounts",
                     "lines_per_order", "regions")
    }
    assert kinds == {
        "Customer Accounts": "table", "events": "partitioned_table", "events_2026q3": "table",
        "active_accounts": "view", "lines_per_order": "materialized_view", "regions": "table",
    }
    parent = by_name[("shapes-postgresql", "events")].id
    for partition in ("events_2026q3", "events_2026q4"):
        assert by_name[("shapes-postgresql", partition)].parent_id == parent
    assert by_name[("shapes-postgresql", "Customer Accounts")].namespace == "Sales"
    assert by_name[("shapes-postgresql", "regions")].namespace == "ref"
    columns = _fields(catalogue, "shapes-postgresql", "Customer Accounts")
    assert set(columns) == {"AccountID", "Region", "e-mail"}
    assert columns["e-mail"].description == "Billing contact"


def test_composite_and_cross_schema_foreign_keys_are_declared(catalogue):
    objects = catalogue["objects"]
    declared = {
        (objects[edge.from_id].name, objects[edge.to_id].name): _pairs(catalogue, edge)
        for edge in catalogue["edges"] if edge.kind == "DECLARES_FK"
    }
    assert declared[("order_lines", "order_headers")] == [
        ("tenant_id", "tenant_id"), ("order_no", "order_no"),
    ]
    assert declared[("Customer Accounts", "regions")] == [("Region", "code")]
    assert declared[("order_headers", "Customer Accounts")] == [("AccountID", "AccountID")]


def test_a_plan_through_a_composite_key_and_quoted_names_executes(catalogue):
    from dbig_planner.config import PlannerConfig
    from dbig_planner.paths import PathRequest, find_paths
    from dbig_planner.plans import PlanRequest, render_plan
    from pg8000 import dbapi

    config = PlannerConfig(graph_dir=catalogue["root"] / "graph")
    by_name = catalogue["by_name"]
    paths = find_paths(
        config.graph_dir,
        PathRequest(
            from_id=by_name[("shapes-postgresql", "order_lines")].id,
            to_id=by_name[("shapes-postgresql", "Customer Accounts")].id,
        ),
        "join",
        config,
    )
    plan = render_plan(
        config.graph_dir, PlanRequest(path_id=paths["paths"][0]["path_id"], max_rows=1000), config
    )
    sql = plan["steps"][0]["sql"]
    assert '"t0"."tenant_id" = "t1"."tenant_id" AND "t0"."order_no" = "t1"."order_no"' in sql
    assert '"Sales"."Customer Accounts"' in sql and '"e-mail"' in sql
    connection = dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(sql)
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.rollback()
        connection.close()
    assert len(rows) == 240, "every order line has its header and its account"
    for row in rows:
        assert (row["order_lines__tenant_id"], row["order_lines__order_no"]) == (
            row["order_headers__tenant_id"], row["order_headers__order_no"],
        )
        assert row["order_headers__AccountID"] == row["Customer Accounts__AccountID"]


def test_clickhouse_types_kinds_and_comment(catalogue):
    by_name = catalogue["by_name"]
    assert {
        name: by_name[("shapes-clickhouse", name)].kind
        for name in ("Page Views", "daily", "daily_mv", "recent", "dedup")
    } == {
        "Page Views": "table", "daily": "table", "daily_mv": "materialized_view",
        "recent": "view", "dedup": "table",
    }
    types = {
        name: item.data_type
        for name, item in _fields(catalogue, "shapes-clickhouse", "Page Views").items()
    }
    assert types == {
        "id": "UInt64", "AccountID": "Nullable(Int32)", "path": "LowCardinality(String)",
        "region_code": "LowCardinality(String)", "tags": "Array(String)",
        "props": "Map(String, String)", "at": "DateTime64(3)",
    }
    assert by_name[("shapes-clickhouse", "Page Views")].description == "Raw views"


def test_a_text_key_is_proposed_across_engines_and_its_plan_executes(catalogue):
    """`region_code LowCardinality(String)` in ClickHouse -> `regions.code text` in PostgreSQL.
    Before type families were grouped, neither the wrapper nor String/text matched."""
    import clickhouse_connect
    from dbig_planner.config import PlannerConfig
    from dbig_planner.paths import PathRequest, find_paths
    from dbig_planner.plans import PlanRequest, render_plan

    objects = catalogue["objects"]
    proposed = {
        (objects[edge.from_id].name, objects[edge.to_id].name, *_pairs(catalogue, edge)[0])
        for edge in catalogue["edges"] if edge.kind == "REFERENCE_CANDIDATE"
    }
    assert ("Page Views", "regions", "region_code", "code") in proposed

    config = PlannerConfig(graph_dir=catalogue["root"] / "graph")
    by_name = catalogue["by_name"]
    paths = find_paths(
        config.graph_dir,
        PathRequest(
            from_id=by_name[("shapes-postgresql", "regions")].id,
            to_id=by_name[("shapes-clickhouse", "Page Views")].id,
        ),
        "join",
        config,
    )
    plan = render_plan(
        config.graph_dir, PlanRequest(path_id=paths["paths"][0]["path_id"], max_rows=1000), config
    )
    step = plan["steps"][1]
    assert step["parameters"][0]["filters_field"] == "region_code"
    assert "Array(LowCardinality(String))" in step["sql"]
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123)
    try:
        result = client.query(step["sql"], parameters={step["parameters"][0]["name"]: ["EU"]})
    finally:
        client.close()
    regions = {row[result.column_names.index("Page Views__region_code")]
               for row in result.result_set}
    assert len(result.result_set) == 250 and regions == {"EU"}


def test_mongodb_nested_paths_unions_and_nulls(catalogue):
    fields = _fields(catalogue, "shapes-mongodb", "tickets")
    assert {"account.id", "account.region", "items[].sku", "items[].qty", "history[]"} <= set(
        fields
    )
    assert fields["status"].data_type == "int|str"
    assert fields["notes"].nullable is True


def test_every_mongodb_collection_is_sampled_not_just_the_first(catalogue):
    """Both collections hold 60 documents and sample_rows is 50: with a source-wide total, the
    one listed second (and every collection after a large one) had no fields at all."""
    assert set(_fields(catalogue, "shapes-mongodb", "capped_log")) == {"_id", "AccountID", "msg"}
    assert {"account.id", "items[].sku", "status"} <= set(
        _fields(catalogue, "shapes-mongodb", "tickets")
    )


def test_mongodb_internal_collections_are_not_catalogued(catalogue):
    names = {name for source, name in catalogue["by_name"] if source == "shapes-mongodb"}
    assert names == {"tickets", "capped_log"}
    assert not any(name.startswith("system.") for name in names)
    (view,) = catalogue["mongo_diagnostics"]
    assert view["code"] == "VIEW_NOT_CATALOGUED" and "view" in view["message"]


def test_a_mongodb_view_does_not_make_the_catalogue_partial(catalogue):
    """User decision 2026-10-05: a view is left out, said so, and is not a gap in the scan."""
    assert catalogue["mongo_completeness"] == "complete"
    assert catalogue["snapshot_completeness"] == "complete"


def _candidates(catalogue: dict) -> set[tuple]:
    objects = catalogue["objects"]
    return {
        (objects[edge.from_id].source_id, objects[edge.from_id].name, *_pairs(catalogue, edge)[0],
         objects[edge.to_id].name)
        for edge in catalogue["edges"] if edge.kind == "REFERENCE_CANDIDATE"
    }


def test_a_column_named_like_a_declared_key_is_linked_across_engines(catalogue):
    """`AccountID` in ClickHouse and MongoDB, beside PostgreSQL's primary key
    `"Customer Accounts"."AccountID"`. The stem `account` is too far from `customer account` for
    the suffix rule, so these were never proposed before the key-name rule."""
    found = _candidates(catalogue)
    for source, table in (("shapes-clickhouse", "dedup"), ("shapes-clickhouse", "daily"),
                          ("shapes-clickhouse", "Page Views"), ("shapes-mongodb", "capped_log"),
                          ("shapes-postgresql", "events")):
        assert (source, table, "AccountID", "AccountID", "Customer Accounts") in found, table


def test_a_declared_key_wins_over_columns_that_measure_unique(catalogue):
    """`regions` declares `code`; on two rows `name` also measures unique, and was proposed."""
    assert not [item for item in _candidates(catalogue) if item[-1] == "regions"
                and item[3] == "name"]


def test_a_join_seen_in_query_logs_is_a_path_whose_plan_runs(catalogue):
    import clickhouse_connect
    from dbig_planner.config import PlannerConfig
    from dbig_planner.paths import PathRequest, find_paths
    from dbig_planner.plans import PlanRequest, render_plan

    config = PlannerConfig(graph_dir=catalogue["root"] / "graph")
    by_name = catalogue["by_name"]
    paths = find_paths(
        config.graph_dir,
        PathRequest(from_id=by_name[("shapes-clickhouse", "Page Views")].id,
                    to_id=by_name[("shapes-clickhouse", "dedup")].id, max_hops=1),
        "join", config,
    )
    (path,) = paths["paths"]
    assert path["status"] == "observed"
    plan = render_plan(config.graph_dir, PlanRequest(path_id=path["path_id"], max_rows=1000),
                       config)
    sql = plan["steps"][0]["sql"]
    assert '"t0"."id" = "t1"."id"' in sql and '"shapes"."Page Views"' in sql
    client = clickhouse_connect.get_client(host="127.0.0.1", port=8123)
    try:
        client.command("INSERT INTO shapes.dedup SELECT number, 1, 1 FROM numbers(10)")
        result = client.query(sql)
    finally:
        client.close()
    assert len(result.result_set) == 10, "ids 0..9 exist on both sides"


def test_the_page_shows_the_hard_names(catalogue):
    for expected in ("Customer Accounts", "e-mail", "Page Views", "events_2026q3",
                     "partitioned_table", "materialized_view", "items[].sku"):
        assert expected in catalogue["page"], expected
