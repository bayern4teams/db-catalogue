import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Source
from jsonschema import Draft202012Validator


def records():
    at = datetime.now(UTC)
    common = {
        "source_id": "site-a",
        "observed_at": at,
        "collector_version": "test",
        "provenance": "synthetic",
    }
    source = Source(id="site-a", kind="postgresql", name="synthetic", **common)
    obj = Object(
        id=stable_id("site-a", "table", "42"), native_id="42", kind="table", name="orders", **common
    )
    field = Field(
        id=stable_id("site-a", "field", "42:1"),
        object_id=obj.id,
        native_id="42:1",
        name="id",
        data_type="int",
        **common,
    )
    return {"sources.jsonl": [source], "objects.jsonl": [obj], "fields.jsonl": [field]}


def test_signed_bundle_roundtrip_and_tampering(tmp_path):
    path = tmp_path / "bundle"
    write_artifact(
        path,
        "collector-bundle",
        records(),
        producer="test",
        source_run_ids=["run-a"],
        signing_key=b"synthetic-key",
        key_id="test",
    )
    manifest, data = read_artifact(path, signing_key=b"synthetic-key", require_signature=True)
    assert manifest.ir_version == "1.0"
    assert len(data["fields.jsonl"]) == 1
    with pytest.raises(ValueError, match="signature key unavailable"):
        read_artifact(path)
    with (path / "fields.jsonl").open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(ValueError, match="size-mismatched"):
        read_artifact(path, signing_key=b"synthetic-key")


def test_unknown_major_and_additive_minor(tmp_path):
    bundle = records()
    bundle["sources.jsonl"][0].ir_version = "1.1"
    bundle["sources.jsonl"][0].future_field = "okay"
    path = tmp_path / "minor"
    write_artifact(path, "collector-bundle", bundle, producer="test", source_run_ids=["r"])
    assert read_artifact(path)[1]["sources.jsonl"][0].ir_version == "1.1"
    bundle["sources.jsonl"][0].ir_version = "2.0"
    with pytest.raises(ValueError, match="unsupported IR major"):
        write_artifact(
            tmp_path / "major", "collector-bundle", bundle, producer="test", source_run_ids=["r"]
        )


def test_dangling_field_fails_before_publish(tmp_path):
    bundle = records()
    bundle["fields.jsonl"][0].object_id = "missing"
    with pytest.raises(ValueError, match="dangling field"):
        write_artifact(
            tmp_path / "bad", "collector-bundle", bundle, producer="test", source_run_ids=["r"]
        )
    assert not (tmp_path / "bad").exists()


def test_published_schema_accepts_synthetic_object():
    schema_path = Path(__file__).resolve().parents[1] / "schemas/ir-v1/object.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(records()["objects.jsonl"][0].model_dump(mode="json"))
