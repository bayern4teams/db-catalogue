import hashlib
import hmac
import json
import unicodedata
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dbig_contracts.artifact import (
    _unsigned,
    canonical,
    read_artifact,
    validate_row,
    write_artifact,
)
from dbig_contracts.ids import stable_id
from dbig_contracts.models import (
    Diagnostic,
    Edge,
    Evidence,
    Field,
    Manifest,
    Object,
    Record,
    Signature,
    Source,
)
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


def test_bundle_rejects_records_from_another_source(tmp_path):
    bundle = records()
    bundle["fields.jsonl"][0].source_id = "other-source"
    with pytest.raises(ValueError, match="mixes source IDs"):
        write_artifact(
            tmp_path / "mixed", "collector-bundle", bundle,
            producer="test", source_run_ids=["r"],
        )


@pytest.mark.parametrize(
    ("parent", "message"),
    [("missing", "invalid object parent"), ("self", "cyclic object parent")],
)
def test_bundle_rejects_invalid_object_parent(tmp_path, parent, message):
    bundle = records()
    obj = bundle["objects.jsonl"][0]
    obj.parent_id = obj.id if parent == "self" else "missing"
    with pytest.raises(ValueError, match=message):
        write_artifact(
            tmp_path / parent,
            "collector-bundle",
            bundle,
            producer="test",
            source_run_ids=["r"],
        )
    assert not (tmp_path / parent).exists()


def test_bundle_accepts_visible_object_parent(tmp_path):
    bundle = records()
    child = bundle["objects.jsonl"][0]
    parent = Object(
        id="parent",
        source_id=child.source_id,
        native_id="41",
        kind="partitioned_table",
        name="parent",
        observed_at=child.observed_at,
        collector_version="test",
        provenance="synthetic",
    )
    child.parent_id = parent.id
    bundle["objects.jsonl"].insert(0, parent)
    write_artifact(
        tmp_path / "partition",
        "collector-bundle",
        bundle,
        producer="test",
        source_run_ids=["r"],
    )
    _, published = read_artifact(tmp_path / "partition")
    assert published["objects.jsonl"][1].parent_id == parent.id


def test_published_schema_accepts_synthetic_object():
    schema_path = Path(__file__).resolve().parents[1] / "schemas/ir-v1/object.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    record = records()["objects.jsonl"][0].model_dump(mode="json")
    assert Draft202012Validator(schema).is_valid(record)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("field_owner", "dangling snapshot field"),
        ("source", "dangling snapshot source"),
        ("node_type", "does not match any of the expected tags"),
        ("evidence_target", "dangling evidence target"),
        ("field_pair", "dangling edge field pair"),
        ("run_reference", "snapshot run references mismatch"),
    ],
)
def test_snapshot_rejects_broken_references_before_publish(tmp_path, damage, message):
    bundle = records()
    source = bundle["sources.jsonl"][0]
    obj = bundle["objects.jsonl"][0]
    field = bundle["fields.jsonl"][0]
    common = {
        "source_id": source.id,
        "observed_at": datetime.now(UTC),
        "collector_version": "test",
        "provenance": "synthetic",
    }
    evidence = Evidence(
        id="evidence", kind="catalog_key", subject_id=field.id, object_id=obj.id, **common
    )
    edge = Edge(
        id="edge",
        kind="DECLARES_FK",
        from_id=obj.id,
        to_id=obj.id,
        status="declared",
        evidence_ids=[evidence.id],
        field_pairs=[[field.id, field.id]],
        **common,
    )
    run = Record(id="run", record_type="run", **common)
    nodes = [source, obj, field]
    run_ids = [run.id]
    if damage == "field_owner":
        field.object_id = "missing"
    elif damage == "source":
        obj.source_id = "missing"
    elif damage == "node_type":
        nodes.append(Record(id="bad", record_type="diagnostic", **common))
    elif damage == "evidence_target":
        evidence.object_id = "missing"
    elif damage == "field_pair":
        edge.field_pairs = [[field.id, "missing"]]
    elif damage == "run_reference":
        run_ids = ["missing"]
    path = tmp_path / damage
    with pytest.raises(ValueError, match=message):
        write_artifact(
            path,
            "catalog-snapshot",
            {
                "nodes.jsonl": nodes,
                "edges.jsonl": [edge],
                "evidence.jsonl": [evidence],
                "runs.jsonl": [run],
            },
            producer="test",
            source_run_ids=run_ids,
        )
    assert not path.exists()


def test_snapshot_rejects_cross_source_parent_and_parent_cycle(tmp_path):
    bundle = records()
    source = bundle["sources.jsonl"][0]
    child = bundle["objects.jsonl"][0]
    other = Source(
        id="other",
        source_id="other",
        kind="postgresql",
        name="other",
        observed_at=source.observed_at,
        collector_version="test",
        provenance="synthetic",
    )
    parent = Object(
        id="other-table",
        source_id="other",
        native_id="43",
        kind="table",
        name="other",
        observed_at=source.observed_at,
        collector_version="test",
        provenance="synthetic",
    )
    child.parent_id = parent.id
    tables = {"nodes.jsonl": [source, child, other, parent]}
    with pytest.raises(ValueError, match="invalid object parent"):
        write_artifact(
            tmp_path / "cross-source",
            "catalog-snapshot",
            tables,
            producer="test",
            source_run_ids=[],
        )

    parent.source_id = source.id
    parent.parent_id = child.id
    with pytest.raises(ValueError, match="cyclic object parent"):
        write_artifact(
            tmp_path / "cycle",
            "catalog-snapshot",
            tables,
            producer="test",
            source_run_ids=[],
        )


# --- strict snapshot node contract ----------------------------------------------------
#
# `nodes.jsonl` carries source, object and field rows. It used to validate against the
# permissive base `Record`, so a row with an unexpected `record_type`, or one missing its own
# type's required fields, was accepted at row level. It now validates against a discriminated
# union, and `schemas/ir-v1/node.schema.json` publishes that contract.


def snapshot_tables():
    bundle = records()
    source = bundle["sources.jsonl"][0]
    obj = bundle["objects.jsonl"][0]
    field = bundle["fields.jsonl"][0]
    common = {
        "source_id": source.id,
        "observed_at": datetime.now(UTC),
        "collector_version": "test",
        "provenance": "synthetic",
    }
    evidence = Evidence(
        id="evidence", kind="catalog_key", subject_id=field.id, object_id=obj.id, **common
    )
    edge = Edge(
        id="edge",
        kind="DECLARES_FK",
        from_id=obj.id,
        to_id=obj.id,
        status="declared",
        evidence_ids=[evidence.id],
        field_pairs=[[field.id, field.id]],
        **common,
    )
    run = Record(id="run", record_type="run", **common)
    return {
        "nodes.jsonl": [source, obj, field],
        "edges.jsonl": [edge],
        "evidence.jsonl": [evidence],
        "runs.jsonl": [run],
    }, common


def publish_snapshot(path, tables):
    return write_artifact(
        path,
        "catalog-snapshot",
        tables,
        producer="test",
        source_run_ids=[record.id for record in tables["runs.jsonl"]],
    )


def test_snapshot_accepts_the_three_node_record_types(tmp_path):
    tables, _ = snapshot_tables()
    publish_snapshot(tmp_path / "snapshot", tables)
    _, result = read_artifact(tmp_path / "snapshot")
    assert {record.record_type for record in result["nodes.jsonl"]} == {
        "source",
        "object",
        "field",
    }


def test_snapshot_node_is_read_back_as_its_own_type(tmp_path):
    """The union resolves each row to its concrete model, not the permissive base Record."""
    tables, _ = snapshot_tables()
    publish_snapshot(tmp_path / "snapshot", tables)
    _, result = read_artifact(tmp_path / "snapshot")
    by_type = {record.record_type: record for record in result["nodes.jsonl"]}
    assert type(by_type["source"]).__name__ == "Source"
    assert type(by_type["object"]).__name__ == "Object"
    assert type(by_type["field"]).__name__ == "Field"


def test_snapshot_refuses_a_node_missing_its_types_required_field(tmp_path):
    """An object row without `kind` satisfies base Record but is not a valid Object."""
    tables, common = snapshot_tables()
    incomplete = {"record_type": "object", "id": "broken", "native_id": "9", "name": "x", **common}
    incomplete["observed_at"] = incomplete["observed_at"].isoformat()
    tables["nodes.jsonl"] = [*tables["nodes.jsonl"], incomplete]
    with pytest.raises(ValueError, match="Field required"):
        publish_snapshot(tmp_path / "snapshot", tables)


def test_snapshot_refuses_an_unknown_node_record_type(tmp_path):
    tables, common = snapshot_tables()
    tables["nodes.jsonl"] = [
        *tables["nodes.jsonl"],
        Record(id="bad", record_type="diagnostic", **common),
    ]
    with pytest.raises(ValueError, match="does not match any of the expected tags"):
        publish_snapshot(tmp_path / "snapshot", tables)


def test_row_validation_refuses_an_unknown_node_type_directly():
    """`validate_row` is the single gate both the write and read paths use for each row."""
    row = records()["objects.jsonl"][0].model_dump(mode="json")
    row["record_type"] = "diagnostic"
    with pytest.raises(ValueError, match="does not match any of the expected tags"):
        validate_row("nodes.jsonl", row)


def test_row_validation_refuses_a_node_missing_its_types_required_field_directly():
    """An object row without `kind` satisfies base `Record` but is not a valid `Object`."""
    row = records()["objects.jsonl"][0].model_dump(mode="json")
    del row["kind"]
    with pytest.raises(ValueError, match="kind"):
        validate_row("nodes.jsonl", row)


def test_row_validation_still_accepts_a_base_record_for_runs():
    """`runs.jsonl` deliberately has no strict model, so the union must not apply to it."""
    row = records()["objects.jsonl"][0].model_dump(mode="json")
    row["record_type"] = "run"
    assert validate_row("runs.jsonl", row).record_type == "run"


def test_published_node_schema_accepts_each_real_node_type():
    schema_path = Path(__file__).resolve().parents[1] / "schemas/ir-v1/node.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    bundle = records()
    for key in ("sources.jsonl", "objects.jsonl", "fields.jsonl"):
        validator.validate(bundle[key][0].model_dump(mode="json"))


def test_published_node_schema_discriminates_on_record_type():
    schema_path = Path(__file__).resolve().parents[1] / "schemas/ir-v1/node.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["discriminator"]["propertyName"] == "record_type"
    assert set(schema["discriminator"]["mapping"]) == {"source", "object", "field"}


def test_published_node_schema_rejects_an_unknown_record_type():
    schema_path = Path(__file__).resolve().parents[1] / "schemas/ir-v1/node.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    row = records()["objects.jsonl"][0].model_dump(mode="json")
    row["record_type"] = "diagnostic"
    assert not Draft202012Validator(schema).is_valid(row)


def _diagnostic(code, suffix="a"):
    return Diagnostic(
        id=stable_id("site-a", "diagnostic", f"{code}:{suffix}"),
        source_id="site-a",
        observed_at=datetime.now(UTC),
        collector_version="test",
        provenance="synthetic",
        code=code,
        message="synthetic",
    )


def test_a_bundle_manifest_counts_its_diagnostics_by_code(tmp_path):
    """A reader must see what a run could not carry without parsing diagnostics.jsonl."""
    bundle = records()
    bundle["diagnostics.jsonl"] = [
        _diagnostic("IDENTITY_UNAVAILABLE", "one"),
        _diagnostic("IDENTITY_UNAVAILABLE", "two"),
        _diagnostic("STATS_UNAVAILABLE"),
    ]
    manifest = write_artifact(
        tmp_path / "bundle", "collector-bundle", bundle, producer="test", source_run_ids=["run-a"]
    )
    assert manifest.diagnostics == {"IDENTITY_UNAVAILABLE": 2, "STATS_UNAVAILABLE": 1}
    read_manifest, _ = read_artifact(tmp_path / "bundle")
    assert read_manifest.diagnostics == {"IDENTITY_UNAVAILABLE": 2, "STATS_UNAVAILABLE": 1}


def test_the_summary_is_sorted_so_manifest_bytes_stay_deterministic(tmp_path):
    bundle = records()
    bundle["diagnostics.jsonl"] = [
        _diagnostic("STATS_UNAVAILABLE"),
        _diagnostic("IDENTITY_UNAVAILABLE"),
        _diagnostic("OUT_OF_SCOPE_REFERENCE"),
    ]
    manifest = write_artifact(
        tmp_path / "bundle", "collector-bundle", bundle, producer="test", source_run_ids=["run-a"]
    )
    assert list(manifest.diagnostics) == sorted(manifest.diagnostics)


def test_a_clean_bundle_reports_an_empty_summary_not_a_missing_one(tmp_path):
    """Empty and unreported must stay distinguishable, because only one of them is a claim."""
    manifest = write_artifact(
        tmp_path / "bundle",
        "collector-bundle",
        records(),
        producer="test",
        source_run_ids=["run-a"],
    )
    assert manifest.diagnostics == {}


def test_a_snapshot_reports_no_diagnostic_summary(tmp_path):
    """Snapshots carry no diagnostics file, so an empty summary there would be a false claim."""
    tables, _ = snapshot_tables()
    manifest = publish_snapshot(tmp_path / "snapshot", tables)
    assert manifest.diagnostics is None
    read_manifest, _ = read_artifact(tmp_path / "snapshot")
    assert read_manifest.diagnostics is None


def test_a_manifest_that_under_reports_dropped_objects_is_refused(tmp_path):
    """The whole point of the summary is that it cannot quietly hide what a run dropped."""
    bundle = records()
    bundle["diagnostics.jsonl"] = [
        _diagnostic("IDENTITY_UNAVAILABLE", "one"),
        _diagnostic("IDENTITY_UNAVAILABLE", "two"),
    ]
    path = tmp_path / "bundle"
    write_artifact(path, "collector-bundle", bundle, producer="test", source_run_ids=["run-a"])
    manifest_path = path / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["diagnostics"] = {"IDENTITY_UNAVAILABLE": 1}
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="disagrees with diagnostics.jsonl"):
        read_artifact(path)


def test_a_manifest_claiming_a_code_that_never_occurred_is_refused(tmp_path):
    path = tmp_path / "bundle"
    write_artifact(path, "collector-bundle", records(), producer="test", source_run_ids=["run-a"])
    manifest_path = path / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["diagnostics"] = {"IDENTITY_UNAVAILABLE": 1}
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="disagrees with diagnostics.jsonl"):
        read_artifact(path)


def test_a_bundle_from_a_producer_predating_the_summary_still_verifies(tmp_path):
    """Compatibility guard for the field's optionality.

    `_unsigned` excludes `None`, so a manifest written before this field existed hashes to the
    same bytes under the new model and its signature still verifies. A default of `{}` would have
    changed those bytes and silently invalidated every previously signed bundle, which is why the
    field is optional rather than empty by default. This signs an older-shaped manifest rather
    than stripping the field from newly signed bytes, so it tests the reader, not the writer.
    """
    path = tmp_path / "bundle"
    key = b"synthetic-key"
    write_artifact(
        path, "collector-bundle", records(), producer="test", source_run_ids=["run-a"]
    )
    manifest_path = path / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert document.pop("diagnostics") == {}, "the current producer reports a summary"
    older = Manifest.model_validate(document)
    assert older.diagnostics is None, "an absent summary must parse as unreported"
    older.signature = Signature(
        key_id="test", value=hmac.new(key, _unsigned(older), hashlib.sha256).hexdigest()
    )
    manifest_path.write_bytes(canonical(older))
    read_manifest, data = read_artifact(path, signing_key=key, require_signature=True)
    assert read_manifest.diagnostics is None
    assert len(data["fields.jsonl"]) == 1, "the rest of the bundle still reads"


@pytest.mark.parametrize("code", ["", "lowercase", "HAS SPACE", "1LEADING", "X" * 65])
def test_a_diagnostic_code_that_cannot_be_summarised_is_refused(tmp_path, code):
    """Counts can only add up to the row count if every row has exactly one well-formed code."""
    with pytest.raises(ValueError, match=r"Diagnostic\ncode"):
        validate_row(
            "diagnostics.jsonl",
            {
                "record_type": "diagnostic",
                "id": "diagnostic:1",
                "source_id": "site-a",
                "observed_at": datetime.now(UTC),
                "collector_version": "test",
                "provenance": "synthetic",
                "code": code,
            },
        )


def test_a_diagnostic_without_a_code_is_refused_on_write(tmp_path):
    bundle = records()
    bundle["diagnostics.jsonl"] = [
        {
            "record_type": "diagnostic",
            "id": "diagnostic:1",
            "source_id": "site-a",
            "observed_at": datetime.now(UTC).isoformat(),
            "collector_version": "test",
            "provenance": "synthetic",
        }
    ]
    with pytest.raises(ValueError, match=r"Diagnostic\ncode\n  Field required"):
        write_artifact(
            tmp_path / "bundle",
            "collector-bundle",
            bundle,
            producer="test",
            source_run_ids=["run-a"],
        )
    assert not (tmp_path / "bundle").exists(), "a refused bundle must publish nothing"


# --- canonical encoding determinism -----------------------------------------------------
#
# Digests, signatures and diffs are all taken over canonical bytes, so one logical value must
# have exactly one encoding. These tests pin the properties that hold and document, by naming
# them, the one that deliberately does not.


def test_the_same_record_always_encodes_to_the_same_bytes():
    at = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    first = Source(
        id="s",
        kind="postgresql",
        name="n",
        source_id="s",
        observed_at=at,
        collector_version="t",
        provenance="p",
    )
    second = Source.model_validate(json.loads(canonical(first)))
    assert canonical(first) == canonical(second), "a round trip must not change the bytes"


def test_key_order_in_the_input_does_not_change_the_bytes():
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


def test_a_non_utc_timestamp_is_refused_so_one_instant_has_one_encoding():
    """Rejected rather than normalised: a normalised record would fail its signature check
    later and report the wrong cause."""
    at = datetime(2026, 9, 27, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    with pytest.raises(ValueError, match="must be UTC"):
        Source(
            id="s",
            kind="postgresql",
            name="n",
            source_id="s",
            observed_at=at,
            collector_version="t",
            provenance="p",
        )


def test_a_naive_timestamp_is_still_refused():
    with pytest.raises(ValueError, match="must include a timezone"):
        Source(
            id="s",
            kind="postgresql",
            name="n",
            source_id="s",
            observed_at=datetime(2026, 9, 27, 12, 0, 0),
            collector_version="t",
            provenance="p",
        )


def test_a_manifest_timestamp_must_also_be_utc(tmp_path):
    path = tmp_path / "bundle"
    write_artifact(path, "collector-bundle", records(), producer="test", source_run_ids=["run-a"])
    document = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    document["created_at"] = "2026-09-27T14:00:00+02:00"
    (path / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="must be UTC"):
        read_artifact(path)


def test_unicode_names_are_not_normalised():
    """Deliberate: two Unicode forms are two different source identifiers, and folding them
    would merge objects the source keeps apart. Recorded so the digest difference is a known
    property rather than a surprise."""
    at = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    common = {
        "source_id": "s",
        "observed_at": at,
        "collector_version": "t",
        "provenance": "p",
        "kind": "postgresql",
    }
    composed = Source(id="s", name=unicodedata.normalize("NFC", "café"), **common)
    decomposed = Source(id="s", name=unicodedata.normalize("NFD", "café"), **common)
    assert canonical(composed) != canonical(decomposed)
