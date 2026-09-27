"""Compatibility tests against committed golden artifacts.

Every other artifact test writes and reads inside one process, so it can only prove that this
version agrees with itself. The requester and planner install as separate wheels and are
expected to be older than the snapshot they read, so the reader contract needs a fixture that
was produced once, committed, and is read back by every later version.

`scripts/make_golden.py` regenerates these artifacts from a frozen clock. A diff there is the
signal these tests exist for: it means the artifact contract changed, and that change must be
deliberate, documented in `TECHNICAL_IMPLEMENTATION.md` §6a, and compatible with a reader that
predates it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from dbig_contracts.artifact import canonical, read_artifact, validate_row, write_artifact
from jsonschema import Draft202012Validator

GOLDEN = Path(__file__).parent / "golden" / "ir-v1"
SCHEMAS = Path(__file__).parents[1] / "schemas" / "ir-v1"
# Matches scripts/make_golden.py. Synthetic: it signs a committed fixture and nothing else.
GOLDEN_KEY = b"golden-fixture-key-not-a-credential"
ARTIFACTS = ("collector-bundle", "catalog-snapshot")
# Which published schema each artifact file's rows must satisfy. `runs.jsonl` has no strict
# model by design, so it has no schema entry and is checked only as a readable record.
SCHEMA_FOR_FILE = {
    "sources.jsonl": "source",
    "objects.jsonl": "object",
    "fields.jsonl": "field",
    "constraints.jsonl": "constraint",
    "diagnostics.jsonl": "diagnostic",
    "edges.jsonl": "edge",
    "evidence.jsonl": "evidence",
    "nodes.jsonl": "node",
}


def _rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


@pytest.mark.parametrize("name", ARTIFACTS)
def test_a_committed_artifact_still_reads_and_verifies(name):
    """The core compatibility claim: today's reader accepts an artifact it did not write."""
    manifest, data = read_artifact(
        GOLDEN / name, signing_key=GOLDEN_KEY, require_signature=True
    )
    assert manifest.ir_version == "1.0"
    assert manifest.content_type == name
    assert any(records for records in data.values()), "the fixture must carry rows"


def test_the_golden_bundle_still_reports_its_diagnostic_summary():
    manifest, data = read_artifact(GOLDEN / "collector-bundle", signing_key=GOLDEN_KEY)
    assert manifest.diagnostics == {"IDENTITY_UNAVAILABLE": 1}
    assert manifest.completeness == "partial"
    assert len(data["diagnostics.jsonl"]) == 1


def test_the_golden_snapshot_carries_all_three_node_types():
    _, data = read_artifact(GOLDEN / "catalog-snapshot", signing_key=GOLDEN_KEY)
    assert {node.record_type for node in data["nodes.jsonl"]} == {"source", "object", "field"}


@pytest.mark.parametrize("name", ARTIFACTS)
def test_every_golden_row_satisfies_its_published_schema(name):
    """The published schemas are the contract an external reader would implement against, so
    they must accept the artifacts this repository actually writes."""
    checked = 0
    for path in sorted((GOLDEN / name).glob("*.jsonl")):
        schema_name = SCHEMA_FOR_FILE.get(path.name)
        if schema_name is None:
            continue
        schema = json.loads((SCHEMAS / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for row in _rows(path):
            validator.validate(row)
            checked += 1
    assert checked, f"no {name} rows were checked against a schema"


@pytest.mark.parametrize("name", ARTIFACTS)
def test_the_manifest_digests_still_match_the_committed_files(name):
    """Catches a golden file edited by hand, which would quietly weaken every test above."""
    manifest, _ = read_artifact(GOLDEN / name, signing_key=GOLDEN_KEY)
    for entry in manifest.files:
        assert (GOLDEN / name / entry.path).stat().st_size == entry.bytes


@pytest.mark.parametrize("name", ARTIFACTS)
def test_rewriting_the_golden_rows_reproduces_the_same_bytes(tmp_path, name):
    """Determinism across a read/write cycle: the same rows must re-encode identically, so a
    consumer that round-trips an artifact does not change its digests."""
    _, data = read_artifact(GOLDEN / name, signing_key=GOLDEN_KEY)
    rewritten = tmp_path / name
    write_artifact(
        rewritten,
        name,
        {file: list(records) for file, records in data.items()},
        producer="golden",
        source_run_ids=[record.id for record in data.get("runs.jsonl", [])] or ["ignored"],
        completeness="partial" if name == "collector-bundle" else "complete",
    )
    for path in sorted((GOLDEN / name).glob("*.jsonl")):
        assert (rewritten / path.name).read_bytes() == path.read_bytes(), path.name


@pytest.mark.parametrize("name", ARTIFACTS)
def test_an_older_reader_tolerates_an_added_field(tmp_path, name):
    """Forward compatibility: a newer producer may add a field, and a reader that does not know
    it must still read the row rather than refuse it."""
    working = tmp_path / name
    shutil.copytree(GOLDEN / name, working)
    target = next(
        path
        for path in sorted(working.glob("*.jsonl"))
        if path.name in SCHEMA_FOR_FILE and _rows(path)
    )
    rows = _rows(target)
    rows[0]["field_from_a_later_version"] = "ignored"
    target.write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    _refresh_manifest(working)
    _, data = read_artifact(working)
    assert data[target.name][0].field_from_a_later_version == "ignored"


def test_a_tampered_golden_row_is_refused(tmp_path):
    """The fixture must not be readable after an edit, or it would prove nothing."""
    working = tmp_path / "collector-bundle"
    shutil.copytree(GOLDEN / "collector-bundle", working)
    rows = _rows(working / "objects.jsonl")
    rows[0]["name"] = "renamed_by_hand"
    (working / "objects.jsonl").write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
    with pytest.raises(ValueError, match="size-mismatched|digest"):
        read_artifact(working, signing_key=GOLDEN_KEY)


def test_a_golden_row_still_validates_through_the_shared_gate():
    """`validate_row` is the single gate both the writer and reader use, so the committed rows
    must pass it as their own concrete types rather than as a permissive base record."""
    for path in sorted((GOLDEN / "collector-bundle").glob("*.jsonl")):
        for row in _rows(path):
            record = validate_row(path.name, row)
            assert record.ir_version == "1.0"


def _refresh_manifest(directory: Path) -> None:
    """Recompute digests after deliberately editing a copy, so the test exercises the reader's
    record validation rather than stopping at its file-integrity check."""
    import hashlib

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("signature", None)
    for entry in manifest["files"]:
        data = (directory / entry["path"]).read_bytes()
        entry["bytes"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        entry["count"] = len([line for line in data.splitlines() if line])
    (directory / "manifest.json").write_bytes(canonical(manifest))
