from datetime import UTC, datetime

import pytest
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.models import Field, Object, Record, Source


def source_bundle(path):
    common = {
        "source_id": "site-a",
        "observed_at": datetime.now(UTC),
        "collector_version": "synthetic",
        "provenance": "synthetic",
    }
    customer = Object(
        id="customer",
        native_id="1",
        kind="table",
        name="customers",
        namespace="public",
        tags=["billing"],
        capabilities=["sql_read"],
        **common,
    )
    order = Object(
        id="order", native_id="2", kind="table", name="orders", namespace="public", **common
    )
    loose = Object(
        id="loose", native_id="3", kind="table", name="loose_orders", namespace="public", **common
    )
    fields = [
        Field(
            id="customer-pk",
            object_id="customer",
            native_id="1:1",
            name="id",
            data_type="integer",
            ordinal=1,
            **common,
        ),
        Field(
            id="order-fk",
            object_id="order",
            native_id="2:1",
            name="customer_id",
            data_type="integer",
            ordinal=1,
            **common,
        ),
        Field(
            id="loose-fk",
            object_id="loose",
            native_id="3:1",
            name="customer_id",
            data_type="integer",
            ordinal=1,
            **common,
        ),
    ]
    constraints = [
        Record.model_validate(
            {
                "record_type": "constraint",
                "id": "pk",
                "native_id": "pk",
                "kind": "primary",
                "object_id": "customer",
                "field_ordinals": [1],
                **common,
            }
        ),
        Record.model_validate(
            {
                "record_type": "constraint",
                "id": "fk",
                "native_id": "fk",
                "kind": "foreign",
                "object_id": "order",
                "target_object_id": "customer",
                "field_ordinals": [1],
                "target_field_ordinals": [1],
                **common,
            }
        ),
    ]
    return write_artifact(
        path,
        "collector-bundle",
        {
            "sources.jsonl": [Source(id="site-a", kind="postgresql", name="test", **common)],
            "objects.jsonl": [customer, order, loose],
            "fields.jsonl": fields,
            "constraints.jsonl": constraints,
        },
        producer="synthetic",
        source_run_ids=["r"],
        signing_key=b"synthetic-key-12345678901234567890",
        key_id="test",
    )


def test_builder_declared_and_candidate_are_distinct(tmp_path, monkeypatch):
    source_bundle(tmp_path / "bundle")
    monkeypatch.setenv("BUNDLE_KEY", "synthetic-key-12345678901234567890")
    config = BuilderConfig(signing_keys={"test": "BUNDLE_KEY"})
    manifest = assemble([tmp_path / "bundle"], tmp_path / "snapshot", config)
    assert manifest.completeness == "complete"
    _, records = read_artifact(tmp_path / "snapshot")
    edges = {edge.kind: edge for edge in records["edges.jsonl"]}
    assert edges["DECLARES_FK"].status == "declared"
    assert edges["REFERENCE_CANDIDATE"].status == "tentative"
    assert edges["REFERENCE_CANDIDATE"].confidence is None
    assert set(edges["REFERENCE_CANDIDATE"].evidence_ids) <= {
        item.id for item in records["evidence.jsonl"]
    }
    assert all(edge.kind != "DERIVES_FROM" for edge in records["edges.jsonl"])


def test_builder_rejects_wrong_signature(tmp_path, monkeypatch):
    source_bundle(tmp_path / "bundle")
    monkeypatch.setenv("BUNDLE_KEY", "wrong-key-12345678901234567890123")
    with pytest.raises(ValueError, match="invalid artifact signature"):
        assemble(
            [tmp_path / "bundle"],
            tmp_path / "snapshot",
            BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
        )
    assert not (tmp_path / "snapshot").exists()


def test_cross_source_mongo_unique_key_is_tentative_not_lineage(tmp_path, monkeypatch):
    source_bundle(tmp_path / "pg")
    common = {
        "source_id": "site-b",
        "observed_at": datetime.now(UTC),
        "collector_version": "synthetic",
        "provenance": "synthetic",
    }
    mongo_customer = Object(
        id="mongo-customer",
        native_id="uuid-1",
        kind="collection",
        name="customers",
        namespace="app",
        **common,
    )
    invoice = Object(
        id="mongo-invoice",
        native_id="uuid-2",
        kind="collection",
        name="invoices",
        namespace="app",
        **common,
    )
    fields = [
        Field(
            id="mongo-id",
            object_id="mongo-customer",
            native_id="uuid-1:_id",
            name="_id",
            data_type="int",
            **common,
        ),
        Field(
            id="mongo-invoice-customer",
            object_id="mongo-invoice",
            native_id="uuid-2:customer_id",
            name="customer_id",
            data_type="string",
            **common,
        ),
    ]
    unique = Record.model_validate(
        {
            "record_type": "constraint",
            "id": "mongo-unique",
            "native_id": "uuid-1:_id_",
            "kind": "unique",
            "object_id": "mongo-customer",
            "field_names": ["_id"],
            **common,
        }
    )
    write_artifact(
        tmp_path / "mongo",
        "collector-bundle",
        {
            "sources.jsonl": [Source(id="site-b", kind="mongodb", name="mongo", **common)],
            "objects.jsonl": [mongo_customer, invoice],
            "fields.jsonl": fields,
            "constraints.jsonl": [unique],
        },
        producer="synthetic",
        source_run_ids=["mongo-run"],
        signing_key=b"synthetic-key-12345678901234567890",
        key_id="test",
    )
    monkeypatch.setenv("BUNDLE_KEY", "synthetic-key-12345678901234567890")
    assemble(
        [tmp_path / "pg", tmp_path / "mongo"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    _, records = read_artifact(tmp_path / "snapshot")
    candidates = [edge for edge in records["edges.jsonl"] if edge.kind == "REFERENCE_CANDIDATE"]
    assert any(
        edge.from_id == "loose" and edge.to_id == "mongo-customer" and edge.status == "tentative"
        for edge in candidates
    )
    assert not any(edge.from_id == "mongo-invoice" for edge in candidates)
    assert not any(edge.kind == "DERIVES_FROM" for edge in records["edges.jsonl"])


def test_builder_publishes_when_model_is_down(tmp_path, monkeypatch):
    source_bundle(tmp_path / "bundle")
    monkeypatch.setenv("BUNDLE_KEY", "synthetic-key-12345678901234567890")
    monkeypatch.setattr(
        "dbig_builder.builder.review_candidates", lambda *args: ([], "MODEL_UNAVAILABLE_OR_INVALID")
    )
    config = BuilderConfig.model_validate(
        {
            "signing_keys": {"test": "BUNDLE_KEY"},
            "review": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "private",
                "model_digest": "sha",
                "api_key_env": "VLLM_KEY",
            },
        }
    )
    manifest = assemble([tmp_path / "bundle"], tmp_path / "snapshot", config)
    assert manifest.completeness == "partial"
    _, records = read_artifact(tmp_path / "snapshot")
    assert any(edge.kind == "REFERENCE_CANDIDATE" for edge in records["edges.jsonl"])
    assert any(
        getattr(run, "review_status", None) == "MODEL_UNAVAILABLE_OR_INVALID"
        for run in records["runs.jsonl"]
    )
