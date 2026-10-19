"""L1 (DEC-14, user 2026-10-14): an optional, bounded cardinality probe for ClickHouse and MongoDB.

Default off. When on: one extra query per table or collection, charged to `max_queries`, at most
`cardinality_probe_max_columns` columns and `cardinality_probe_max_rows` rows. Only counts leave
the source; a profile carries a distinct count and a uniqueness, never a value.
"""

from __future__ import annotations

import pytest
from dbig_collectors.config import SourceConfig
from dbig_collectors.runtime import Budget, discover_clickhouse, discover_mongodb
from dbig_contracts.models import Profile


def config(kind, **overrides):
    return SourceConfig.model_validate(
        {
            "source_id": "site-a", "kind": kind, "connection_env": "DBIG_CONNECTION",
            "signing_key_env": "DBIG_SIGNING_KEY", "signing_key_id": "test",
            "namespaces": ["public"], **overrides,
        }
    )


class Ch:
    def __init__(self, probe_error=False, probe_row=(100, 100, 7)):
        self.queries = []
        self.probe_error = probe_error
        self.probe_row = probe_row

    def query(self, statement, **kwargs):
        self.queries.append((statement, kwargs))
        outer = self

        class Result:
            result_set = []

        if "system.tables" in statement:
            Result.result_set = [("t", "uuid-1", "MergeTree", "", "", "", [], [], 100, "")]
        elif "system.columns" in statement:
            Result.result_set = [
                ("t", "rowkey", "UInt64", 1, 0, ""),
                ("t", "kind", "String", 2, 0, ""),
                ("t", "tags", "Array(String)", 3, 0, ""),
            ]
        else:
            if outer.probe_error:
                raise RuntimeError("canary-secret")
            Result.result_set = [outer.probe_row]
        return Result()


def test_the_probe_is_off_by_default_and_reads_no_data_query():
    client = Ch()
    discover_clickhouse(client, config("clickhouse"), Budget(10))
    assert all("uniq(" not in statement for statement, _ in client.queries)


def test_clickhouse_probe_yields_uniqueness_from_counts_only():
    client = Ch()
    records = discover_clickhouse(
        client, config("clickhouse", cardinality_probe=True), Budget(10)
    )
    statement, kwargs = client.queries[-1]
    # P6-6 (F9): counted through a LIMIT subquery on one thread (intended flip of the L1 shape).
    assert statement.startswith(
        'SELECT count(), uniq("rowkey"), uniq("kind") '
        'FROM (SELECT "rowkey","kind" FROM "public"."t"'
    )
    assert "tags" not in statement  # array columns are skipped
    assert kwargs["parameters"]["lim"] == 100_000
    assert kwargs["settings"]["max_threads"] == 1
    assert "max_rows_to_read" not in kwargs["settings"]
    by_name = {
        field.name: field.id for field in records["fields.jsonl"]
    }
    probes = [p for p in records["profiles.jsonl"] if p.method == "sample"]
    assert {p.field_id: (p.distinct_estimate, p.uniqueness, p.sample_count) for p in probes} == {
        by_name["rowkey"]: (100.0, 1.0, 100),
        by_name["kind"]: (7.0, 0.07, 100),
    }
    assert isinstance(probes[0], Profile)


def test_the_probe_respects_the_column_cap_and_the_budget():
    client = Ch(probe_row=(10, 10))
    discover_clickhouse(
        client, config("clickhouse", cardinality_probe=True, cardinality_probe_max_columns=1),
        Budget(10),
    )
    assert client.queries[-1][0].startswith('SELECT count(), uniq("rowkey") FROM')
    spent = Ch()
    discover_clickhouse(spent, config("clickhouse", cardinality_probe=True), Budget(2))
    assert all("uniq(" not in statement for statement, _ in spent.queries)


def test_a_failing_probe_is_a_diagnostic_not_a_failed_scan_and_leaks_nothing():
    records = discover_clickhouse(
        Ch(probe_error=True), config("clickhouse", cardinality_probe=True), Budget(10)
    )
    assert [d.code for d in records["diagnostics.jsonl"]] == ["STATS_UNAVAILABLE"]
    assert "canary-secret" not in str(records)
    assert all(p.method != "sample" for p in records["profiles.jsonl"])


class MongoCollection:
    def __init__(self, fail=False):
        self.pipelines = []
        self.fail = fail

    def list_indexes(self):
        return [{"name": "_id_", "unique": True, "key": {"_id": 1}}]

    def estimated_document_count(self, **kwargs):
        return 5

    def aggregate(self, pipeline, **kwargs):
        self.pipelines.append((pipeline, kwargs))
        if self.fail:
            raise RuntimeError("canary-secret")
        return iter([{"rows": [{"n": 50}], "p0": [{"n": 50}]}])


class MongoDb:
    def __init__(self, collection):
        self.collection = collection

    def list_collections(self):
        return [{"name": "orders", "type": "collection", "info": {"uuid": "uuid-9"},
                 "options": {"validator": {"$jsonSchema": {"properties": {
                     "_id": {"bsonType": "int"}, "tags": {"bsonType": "array"}}}}}}]

    def __getitem__(self, name):
        return self.collection


class MongoClient:
    def __init__(self, collection):
        self.db = MongoDb(collection)

    def __getitem__(self, database):
        return self.db


def test_mongo_probe_sends_one_bounded_facet_and_keeps_counts_only():
    collection = MongoCollection()
    records = discover_mongodb(
        MongoClient(collection), config("mongodb", cardinality_probe=True), Budget(10)
    )
    pipeline, kwargs = collection.pipelines[0]
    assert pipeline[0] == {"$limit": 100_000}
    assert kwargs == {"maxTimeMS": 10_000}
    assert set(pipeline[1]["$facet"]) == {"rows", "p0"}  # `tags` is an array path: skipped
    probe = [p for p in records["profiles.jsonl"] if p.method == "sample"]
    assert [(p.distinct_estimate, p.uniqueness, p.sample_count) for p in probe] == [(50.0, 1.0, 50)]


def test_mongo_probe_is_off_by_default_and_failure_is_a_diagnostic():
    off = MongoCollection()
    discover_mongodb(MongoClient(off), config("mongodb"), Budget(10))
    assert off.pipelines == []
    failing = MongoCollection(fail=True)
    records = discover_mongodb(
        MongoClient(failing), config("mongodb", cardinality_probe=True), Budget(10)
    )
    assert "STATS_UNAVAILABLE" in [d.code for d in records["diagnostics.jsonl"]]
    assert "canary-secret" not in str(records)


@pytest.mark.parametrize("field", ["cardinality_probe_max_columns", "cardinality_probe_max_rows"])
def test_the_bounds_are_validated(field):
    with pytest.raises(ValueError):
        config("clickhouse", **{field: 0})


def test_a_scan_runs_the_probe_only_when_it_asked_for_the_sample_capability(tmp_path, monkeypatch):
    """The probe reads data, so it follows the `sample` capability like `sample_rows` does."""
    from datetime import UTC, datetime, timedelta

    from dbig_collectors.scan import accept_scan, process_next
    from dbig_contracts.scan import ScanRequest, sign_scan

    key = "synthetic-key-with-more-than-thirty-two-bytes"
    monkeypatch.setenv("DBIG_SIGNING_KEY", key)
    seen = []
    monkeypatch.setattr("dbig_collectors.scan.collect", lambda cfg, path: seen.append(cfg))
    base = config("postgresql", cardinality_probe=True, authorized_requesters=["catalogue"])
    queue = tmp_path / "queue.sqlite3"
    for index, capabilities in enumerate((["metadata"], ["metadata", "sample"])):
        now = datetime.now(UTC)
        request = ScanRequest(
            id=f"request-{index}", source_id="site-a", scopes=["public"],
            capabilities=capabilities, reason="refresh", requester_id="catalogue",
            idempotency_key=f"idem-{index}", issued_at=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=10), reply_location="local-receipt",
        )
        accept_scan(sign_scan(request, "test", key.encode()), base, queue)
        process_next(base, queue, tmp_path / "out")
    assert [cfg.cardinality_probe for cfg in seen] == [False, True]
