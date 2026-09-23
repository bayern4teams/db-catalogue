import sys
import types
from pathlib import Path

from click.testing import CliRunner
from dbig_collectors.cli import main
from dbig_collectors.config import SourceConfig, load_config
from dbig_collectors.runtime import (
    Budget,
    collect,
    discover_clickhouse,
    discover_mongodb,
    discover_postgresql,
)
from dbig_contracts.artifact import read_artifact


def config(kind: str = "postgresql") -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "source_id": "site-a",
            "kind": kind,
            "connection_env": "DBIG_CONNECTION",
            "signing_key_env": "DBIG_SIGNING_KEY",
            "signing_key_id": "test",
            "namespaces": ["public"],
        }
    )


class PgCursor:
    def __init__(self):
        self.calls = []
        self.index = 0

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchall(self):
        self.index += 1
        if self.index == 1:
            return [(42, "public", "orders", "r")]
        if self.index == 2:
            return [(42, 1, "id", "integer", True)]
        return [(99, 42, 42, "p", [1], None)]

    def close(self):
        pass


class PgConnection:
    def __init__(self):
        self.fake_cursor = PgCursor()
        self.rolled_back = False

    def cursor(self):
        return self.fake_cursor

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def test_postgres_catalog_only_and_stable_ids():
    connection = PgConnection()
    records = discover_postgresql(connection, config(), Budget(3))
    assert connection.rolled_back
    assert connection.fake_cursor.calls[0][0] == "SET TRANSACTION READ ONLY"
    assert records["fields.jsonl"][0].object_id == records["objects.jsonl"][0].id
    assert records["constraints.jsonl"][0].kind == "primary"
    assert all("SELECT *" not in query for query, _ in connection.fake_cursor.calls)


def test_postgres_view_definition_is_hashed_without_literals():
    class ViewCursor(PgCursor):
        def fetchall(self):
            self.index += 1
            return {
                1: [(42, "public", "orders_view", "v")],
                2: [(42, "SELECT * FROM orders WHERE token = 'canary-secret'")],
                3: [(42, 1, "id", "integer", True)],
                4: [],
            }[self.index]

    connection = PgConnection()
    connection.fake_cursor = ViewCursor()
    records = discover_postgresql(connection, config(), Budget(4))
    attributes = records["objects.jsonl"][0].attributes
    assert attributes["view_definition_available"] is True
    assert len(attributes["view_definition_sha256"]) == 64
    assert "canary-secret" not in str(records)


class ChClient:
    def query(self, statement, **kwargs):
        class Result:
            result_set = (
                [("orders", "uuid-42", "MergeTree", "toYYYYMM(day)", "id", "id", [], [])]
                if "system.tables" in statement
                else [("orders", "id", "UInt64", 1)]
            )

        return Result()


def test_clickhouse_uses_uuid_identity():
    records = discover_clickhouse(ChClient(), config("clickhouse"), Budget(2))
    assert records["fields.jsonl"][0].object_id == records["objects.jsonl"][0].id
    assert records["objects.jsonl"][0].attributes["partition_key_sha256"]
    assert "toYYYYMM" not in str(records["objects.jsonl"][0].attributes)


class MongoDb:
    def list_collections(self):
        return [{"name": "orders", "info": {"uuid": "uuid-42"}}]

    def __getitem__(self, name):
        return self

    def list_indexes(self):
        return [{"name": "_id_", "unique": True, "key": {"_id": 1}}]


class MongoClient:
    def __getitem__(self, database):
        return MongoDb()


def test_mongo_metadata_only_never_samples():
    records = discover_mongodb(MongoClient(), config("mongodb"), Budget(2))
    assert records["objects.jsonl"][0].name == "orders"
    assert [field.name for field in records["fields.jsonl"]] == ["_id"]
    assert records["fields.jsonl"][0].data_type == "unknown"
    assert records["constraints.jsonl"][0].field_names == ["_id"]


def test_mongo_validator_and_type_only_sample():
    class Cursor:
        def limit(self, count):
            return self

        def max_time_ms(self, timeout):
            return self

        def __iter__(self):
            return iter(
                [
                    {
                        "_id": 1,
                        "profile": {"age": 42},
                        "items": [{"code": "canary-secret"}, {"code": None}],
                    },
                    {"_id": 2, "items": []},
                ]
            )

        def close(self):
            pass

    class Db(MongoDb):
        def list_collections(self):
            return [
                {
                    "name": "orders",
                    "type": "collection",
                    "info": {"uuid": "uuid-42"},
                    "options": {
                        "validator": {
                            "$jsonSchema": {
                                "required": ["_id"],
                                "properties": {
                                    "_id": {"bsonType": "int"},
                                    "profile": {
                                        "bsonType": "object",
                                        "properties": {"age": {"bsonType": "int"}},
                                    },
                                },
                            }
                        }
                    },
                }
            ]

        def find(self, filter):
            return Cursor()

    class Client:
        def __getitem__(self, database):
            return Db()

    cfg = config("mongodb").model_copy(update={"sample_rows": 2})
    records = discover_mongodb(Client(), cfg, Budget(3))
    fields = {item.name: item for item in records["fields.jsonl"]}
    assert fields["profile.age"].nullable is True
    assert fields["items[].code"].data_type == "NoneType|str"
    assert fields["_id"].nullable is False
    assert "canary-secret" not in str(records)


def test_mongo_sample_byte_budget_stops_without_publishing_values():
    class Cursor:
        def limit(self, count):
            return self

        def max_time_ms(self, timeout):
            return self

        def __iter__(self):
            return iter([{"token": "canary-secret"}])

        def close(self):
            pass

    class Db(MongoDb):
        def find(self, filter):
            return Cursor()

    class Client:
        def __getitem__(self, database):
            return Db()

    cfg = config("mongodb").model_copy(update={"sample_rows": 1, "max_sample_bytes": 1})
    records = discover_mongodb(Client(), cfg, Budget(3))
    assert [field.name for field in records["fields.jsonl"]] == ["_id"]
    assert records["diagnostics.jsonl"][0].code == "SAMPLE_BYTE_LIMIT"
    assert "canary-secret" not in str(records)


def test_config_and_cli_help(tmp_path: Path):
    path = tmp_path / "source.toml"
    path.write_text(
        '[source]\nsource_id="site-a"\nkind="postgresql"\n'
        'connection_env="DBIG_CONNECTION"\nsigning_key_env="DBIG_SIGNING_KEY"\n'
        'signing_key_id="test"\nnamespaces=["public"]\n',
        encoding="utf-8",
    )
    assert load_config(path).sample_rows == 0
    assert CliRunner().invoke(main, ["collect", "--help"]).exit_code == 0


def test_collect_signed_bundle_contains_no_secret(tmp_path, monkeypatch):
    fake_pg = types.ModuleType("pg8000")
    fake_pg.dbapi = types.SimpleNamespace(connect=lambda **kwargs: PgConnection())
    monkeypatch.setitem(sys.modules, "pg8000", fake_pg)
    monkeypatch.setenv("DBIG_CONNECTION", '{"host":"synthetic","password":"canary-secret"}')
    monkeypatch.setenv("DBIG_SIGNING_KEY", "synthetic-signing-key-with-at-least-32-bytes")
    destination = tmp_path / "bundle"
    collect(config(), destination)
    manifest, tables = read_artifact(
        destination,
        signing_key=b"synthetic-signing-key-with-at-least-32-bytes",
        require_signature=True,
    )
    assert manifest.completeness == "complete"
    assert len(tables["constraints.jsonl"]) == 1
    assert all(b"canary-secret" not in path.read_bytes() for path in destination.iterdir())
