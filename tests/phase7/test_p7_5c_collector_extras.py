"""P7-5 option C, worker's own checks for branches the acceptance file leaves open.

Uses the fakes of the acceptance file (read-only import). Invented names only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))

import catalogue_fixture_kit as kit  # noqa: E402
import pytest  # noqa: E402
import test_p7_5c_format_profiles as acc  # noqa: E402
from dbig_collectors.runtime import (  # noqa: E402
    Budget,
    _shape_token,
    discover_clickhouse,
    discover_postgresql,
)
from dbig_contracts.artifact import read_artifact, write_artifact  # noqa: E402
from dbig_contracts.models import Source, ValueRanges, utc_now  # noqa: E402


class _FailingPg(acc.FakePg):
    """A connection whose profile statement for one column fails, as a timeout would."""

    def cursor(self):
        cursor = super().cursor()
        real = cursor.execute

        def execute(statement, params=()):
            if acc.is_profile_statement(statement) and '"label"' in statement:
                self.calls.append(("failed", statement, params))
                raise RuntimeError("canceling statement due to statement timeout")
            real(statement, params)

        cursor.execute = execute
        return cursor

    def rollback(self):
        self.calls.append(("rollback", "", ()))


def test_a_failed_pg_column_read_leaves_a_code_only_diagnostic_and_the_scan_goes_on():
    connection = _FailingPg(acc.PG_TABLES)
    records = discover_postgresql(connection, acc.profiled("postgresql"), Budget(1000))
    kinds = [kind for kind, _, _ in connection.calls]
    assert "rollback" in kinds
    # The transaction is armed again after the rollback, before the next statement.
    after = kinds[kinds.index("failed") :]
    assert after.index("setting") < after.index("profile")
    ours = [
        d for d in records["diagnostics.jsonl"] if d.model_dump()["message"].startswith("format")
    ]
    assert len(ours) == 1 and ours[0].model_dump()["code"] == "STATS_UNAVAILABLE"
    assert "label" not in json.dumps(ours[0].model_dump(mode="json"))
    assert len(acc.profiles_by_column(records)) == len(acc.PG_ELIGIBLE) - 1


def test_a_clickhouse_column_with_no_groups_returns_no_row_and_makes_no_range():
    """An empty non-nullable column: the statement groups by shape, so it returns no row."""
    client = acc.FakeClickHouse(acc.CH_TABLES)
    real = client.query

    def query(statement, parameters=None, settings=None, **extra):
        result = real(statement, parameters, settings, **extra)
        if acc.is_profile_statement(statement):
            result.result_set = []
        return result

    client.query = query
    records = discover_clickhouse(client, acc.profiled("clickhouse"), Budget(1000))
    assert acc.profiles_by_column(records) == {}
    assert records.get(acc.RANGES_FILE, []) == []


@pytest.mark.parametrize(
    ("value", "token"),
    [("QK-4821", "a{2}-9{4}"), ("4821-QK", "9{4}-a{2}"), ("QK_4821", "a{2}_9{4}"), ("", ""),
     ("ab  1", "a{2}**9{1}")],
)
def test_the_shape_token_is_value_free_and_run_length_compressed(value, token):
    assert _shape_token(value, 64) == token


def test_a_long_value_is_cut_to_the_configured_prefix():
    assert _shape_token("abcdef123456", 4) == "a{4}"


def test_a_bundle_without_the_ranges_file_is_still_read(tmp_path):
    source = Source(
        id="alpha", source_id="alpha", observed_at=utc_now(), collector_version="t",
        provenance="synthetic", kind="postgresql", name="alpha",
    )
    write_artifact(tmp_path / "new", "collector-bundle", {"sources.jsonl": [source]},
                   producer="t", source_run_ids=["r"])
    old = tmp_path / "old"
    old.mkdir()
    for item in (tmp_path / "new").iterdir():
        if item.name != acc.RANGES_FILE:
            (old / item.name).write_bytes(item.read_bytes())
    manifest = json.loads((old / "manifest.json").read_text())
    manifest["files"] = [f for f in manifest["files"] if f["path"] != acc.RANGES_FILE]
    (old / "manifest.json").write_text(json.dumps(manifest))
    _, tables = read_artifact(old)
    assert acc.RANGES_FILE not in tables
    with pytest.raises(ValueError, match="file set"):
        manifest["files"] = manifest["files"][:-1]
        (old / "manifest.json").write_text(json.dumps(manifest))
        read_artifact(old)


def test_a_range_cannot_be_inverted_or_a_float():
    common = {"id": "r", "source_id": "s", "observed_at": utc_now(), "collector_version": "t",
              "provenance": "x", "field_id": "f", "object_id": "o"}
    with pytest.raises(ValueError):
        ValueRanges(minimum=5, maximum=1, **common)
    with pytest.raises(ValueError):
        ValueRanges(minimum=1.0, maximum=2, **common)


def test_a_sampled_range_marks_but_never_drops(tmp_path, monkeypatch):
    sources, ranges = acc.pair(kind="bigint", child_range=(1, 5), parent_range=(100, 200))
    snap = acc.snapshot_of(tmp_path, monkeypatch, sources, ranges, name="full")
    assert acc.LINK not in kit.candidates(snap)
    # Same ranges, flagged as read from a sample: the candidate stays, with overlap false.
    monkeypatch.setenv("BUNDLE_KEY", kit.BUNDLE_KEY)
    bundles = []
    for source_id, spec in sources.items():
        path = tmp_path / f"bundle-{source_id}-s"
        acc.make_bundle(path, source_id, spec["tables"], ranges.get(source_id))
        _, data = read_artifact(path, signing_key=kit.BUNDLE_KEY.encode(), require_signature=True)
        rows = [row.model_copy(update={"complete": False}) for row in data[acc.RANGES_FILE]]
        rebuilt = tmp_path / f"rebuilt-{source_id}"
        write_artifact(rebuilt, "collector-bundle", {**data, acc.RANGES_FILE: rows},
                       producer="t", source_run_ids=[f"run-{source_id}"],
                       signing_key=kit.BUNDLE_KEY.encode(), key_id="test")
        bundles.append(rebuilt)
    from dbig_builder.builder import assemble
    from dbig_builder.config import BuilderConfig

    out = tmp_path / "sampled"
    assemble(bundles, out, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}))
    assert acc.LINK in kit.candidates(out)
    assert '"numeric_ranges_overlap": false' in kit.candidate_text(out)
