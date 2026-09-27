"""Ephemeral CI services verify actual metadata driver/catalog compatibility."""

import os
import time

import pytest
from dbig_collectors.config import SourceConfig
from dbig_collectors.runtime import (
    Budget,
    discover_clickhouse,
    discover_mongodb,
    discover_postgresql,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DBIG_RUN_INTEGRATION") != "1", reason="ephemeral CI services unavailable"
)


def _config(kind: str) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "ci-source",
            "kind": kind,
            "connection_env": "UNUSED_CONNECTION",
            "signing_key_env": "UNUSED_SIGNING_KEY",
            "signing_key_id": "ci",
            "namespaces": ["dbig_ci"],
        }
    )


def _retry(connect):
    deadline = time.monotonic() + 90
    while True:
        try:
            return connect()
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)


def test_postgresql_16_catalog_and_read_only_role():
    from pg8000 import dbapi

    admin = _retry(
        lambda: dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    )
    try:
        cursor = admin.cursor()
        cursor.execute("CREATE SCHEMA dbig_ci")
        cursor.execute("CREATE TABLE dbig_ci.customers (id integer PRIMARY KEY)")
        cursor.execute("INSERT INTO dbig_ci.customers VALUES (1)")
        cursor.execute("ANALYZE dbig_ci.customers")
        cursor.execute(
            "CREATE TABLE dbig_ci.orders (customer_id integer REFERENCES dbig_ci.customers(id))"
        )
        cursor.execute("CREATE VIEW dbig_ci.customer_view AS SELECT id FROM dbig_ci.customers")
        cursor.execute("CREATE TABLE dbig_ci.events (id integer) PARTITION BY RANGE (id)")
        cursor.execute("CREATE TABLE dbig_ci.events_p0 PARTITION OF dbig_ci.events "
                       "FOR VALUES FROM (0) TO (10)")
        cursor.execute("CREATE ROLE dbig_reader LOGIN")
        cursor.execute("GRANT USAGE ON SCHEMA dbig_ci TO dbig_reader")
        cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA dbig_ci TO dbig_reader")
        admin.commit()
    finally:
        admin.close()
    reader = dbapi.connect(host="127.0.0.1", port=5432, user="dbig_reader", database="postgres")
    try:
        records = discover_postgresql(reader, _config("postgresql"), Budget(4))
    finally:
        reader.close()
    assert {item.name for item in records["objects.jsonl"]} == {
        "customers",
        "orders",
        "customer_view",
        "events",
        "events_p0",
    }
    assert any(item.kind == "foreign" for item in records["constraints.jsonl"])
    assert any(item.attributes.get("view_definition_sha256") for item in records["objects.jsonl"])
    customers = next(item for item in records["objects.jsonl"] if item.name == "customers")
    assert customers.attributes["row_count_estimate"] == 1.0
    assert customers.attributes["row_count_method"] == "pg_class.reltuples"
    events = next(item for item in records["objects.jsonl"] if item.name == "events")
    partition = next(item for item in records["objects.jsonl"] if item.name == "events_p0")
    assert partition.parent_id == events.id
    assert partition.attributes["is_partition"] is True


def test_clickhouse_25_8_system_tables():
    import clickhouse_connect

    client = _retry(lambda: clickhouse_connect.get_client(host="127.0.0.1", port=8123))
    try:
        client.command("CREATE DATABASE dbig_ci ENGINE=Atomic")
        client.command(
            "CREATE TABLE dbig_ci.orders (id UInt64, day Date) ENGINE=MergeTree "
            "PARTITION BY toYYYYMM(day) ORDER BY id"
        )
        records = discover_clickhouse(client, _config("clickhouse"), Budget(2))
    finally:
        client.close()
    assert [item.name for item in records["objects.jsonl"]] == ["orders"]
    assert records["objects.jsonl"][0].attributes["partition_key_sha256"]
    assert records["objects.jsonl"][0].attributes["row_count_estimate"] == 0
    assert records["objects.jsonl"][0].attributes["row_count_method"] == "system.tables.total_rows"
    assert {item.name for item in records["fields.jsonl"]} == {"id", "day"}


def test_mongodb_7_collection_validator_and_index():
    from pymongo import MongoClient

    client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=2000)
    _retry(lambda: client.admin.command("ping"))
    try:
        collection = client.dbig_ci.create_collection(
            "orders",
            validator={"$jsonSchema": {"properties": {"customer_id": {"bsonType": "int"}}}},
        )
        collection.create_index("customer_id", unique=True)
        records = discover_mongodb(client, _config("mongodb"), Budget(3))
    finally:
        client.close()
    assert [item.name for item in records["objects.jsonl"]] == ["orders"]
    assert records["objects.jsonl"][0].attributes["row_count_estimate"] == 0
    assert records["objects.jsonl"][0].attributes["row_count_method"] == "estimated_document_count"
    assert any(
        item.name == "customer_id" and item.data_type == "int" for item in records["fields.jsonl"]
    )
    assert any(
        item.kind == "unique" and item.field_names == ["customer_id"]
        for item in records["constraints.jsonl"]
    )


# --- rename stability -------------------------------------------------------------------
#
# The identity contract claims a renamed object keeps its catalogue ID because the ID derives
# from a source-native identifier rather than a name. These tests are that claim's evidence:
# the official documentation for each engine is unreachable from the build environment, and a
# rename performed against the real server is stronger proof than a citation would be. Each
# test creates and drops its own objects so it cannot disturb the exact-name assertions above,
# in either execution order.


def test_postgresql_object_and_field_ids_survive_a_rename():
    from pg8000 import dbapi

    admin = _retry(
        lambda: dbapi.connect(host="127.0.0.1", port=5432, user="postgres", database="postgres")
    )
    try:
        cursor = admin.cursor()
        cursor.execute("CREATE SCHEMA IF NOT EXISTS dbig_ci")
        cursor.execute("CREATE TABLE dbig_ci.rename_me (before_rename integer)")
        admin.commit()
        before = discover_postgresql(admin, _config("postgresql"), Budget(4))
        cursor.execute("ALTER TABLE dbig_ci.rename_me RENAME TO renamed")
        cursor.execute("ALTER TABLE dbig_ci.renamed RENAME COLUMN before_rename TO after_rename")
        admin.commit()
        after = discover_postgresql(admin, _config("postgresql"), Budget(4))
    finally:
        try:
            cursor = admin.cursor()
            cursor.execute("DROP TABLE IF EXISTS dbig_ci.renamed")
            cursor.execute("DROP TABLE IF EXISTS dbig_ci.rename_me")
            admin.commit()
        finally:
            admin.close()

    original = next(item for item in before["objects.jsonl"] if item.name == "rename_me")
    renamed = next(item for item in after["objects.jsonl"] if item.name == "renamed")
    assert renamed.id == original.id, "pg_class.oid must carry identity through a table rename"
    assert renamed.native_id == original.native_id
    assert not any(item.name == "rename_me" for item in after["objects.jsonl"])

    original_field = next(item for item in before["fields.jsonl"] if item.name == "before_rename")
    renamed_field = next(item for item in after["fields.jsonl"] if item.name == "after_rename")
    assert renamed_field.id == original_field.id, "attnum must carry identity through a rename"
    assert renamed_field.object_id == renamed.id


def test_clickhouse_object_id_survives_a_rename():
    import clickhouse_connect

    client = _retry(lambda: clickhouse_connect.get_client(host="127.0.0.1", port=8123))
    try:
        client.command("CREATE DATABASE IF NOT EXISTS dbig_ci ENGINE=Atomic")
        client.command(
            "CREATE TABLE dbig_ci.rename_me (id UInt64) ENGINE=MergeTree ORDER BY id"
        )
        before = discover_clickhouse(client, _config("clickhouse"), Budget(2))
        client.command("RENAME TABLE dbig_ci.rename_me TO dbig_ci.renamed")
        after = discover_clickhouse(client, _config("clickhouse"), Budget(2))
    finally:
        try:
            client.command("DROP TABLE IF EXISTS dbig_ci.renamed")
            client.command("DROP TABLE IF EXISTS dbig_ci.rename_me")
        finally:
            client.close()

    original = next(item for item in before["objects.jsonl"] if item.name == "rename_me")
    renamed = next(item for item in after["objects.jsonl"] if item.name == "renamed")
    assert renamed.id == original.id, "an Atomic database's table UUID must survive RENAME TABLE"
    assert renamed.native_id == original.native_id


def test_mongodb_collection_id_survives_a_rename():
    from pymongo import MongoClient

    client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=2000)
    _retry(lambda: client.admin.command("ping"))
    try:
        client.dbig_ci.create_collection("rename_me")
        before = discover_mongodb(client, _config("mongodb"), Budget(3))
        client.dbig_ci.rename_me.rename("renamed")
        after = discover_mongodb(client, _config("mongodb"), Budget(3))
    finally:
        try:
            client.dbig_ci.drop_collection("renamed")
            client.dbig_ci.drop_collection("rename_me")
        finally:
            client.close()

    original = next(item for item in before["objects.jsonl"] if item.name == "rename_me")
    renamed = next(item for item in after["objects.jsonl"] if item.name == "renamed")
    assert renamed.id == original.id, "a collection UUID must survive renameCollection"
    assert renamed.native_id == original.native_id
