# ruff: noqa
"""Attack H1(a) for DEC-2 / D1a: ClickHouse primary/sorting key COLUMN NAMES are kept (names, not
values) and make a weak, labelled parent claim.

Derived from engineer-fixes D1 (check: ClickHouse `Company` shows `companyID` as its key and
`Company.companyID` becomes a candidate parent, flagged weak) and the DEC-2 text. The collector is
live-only; the builder side is driven through an `index` constraint exactly as the collector's
contract describes (`<table>:sorting_key`). Invented names. Run by path:
    uv run pytest tests/catalogue_fixes/attack3_a_dec2.py -q -p no:cacheprovider
"""

from __future__ import annotations

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import build_snapshot, candidate_text, candidates
from dbig_contracts.artifact import read_artifact


def _tables(**parent):
    return {
        "gizmo": {"fields": [("gizmoid", "integer"), ("label", "text")], **parent},
        "gizmoline": {"fields": [("id", "integer"), ("gizmoid", "integer")]},
    }


def _status(snapshot):
    _, data = read_artifact(snapshot)
    return sorted(e.status for e in data["edges.jsonl"] if e.kind == "REFERENCE_CANDIDATE")


def test_weak_key_parent_is_proposed_and_labelled_weak(tmp_path, monkeypatch):
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="gizmoid")}})
    assert ("gizmoline", "gizmoid", "gizmo", "gizmoid") in candidates(snap)
    text = candidate_text(snap)
    assert "weak" in text, "the claim must be labelled weak in the evidence"


def test_no_key_names_no_parent_old_behaviour(tmp_path, monkeypatch):
    """Fails on the old code only in the sense that old code could never see the key; here the
    control: without the key claim, the unmeasured ClickHouse table is never a parent."""
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables()}})
    assert candidates(snap) == []


def test_weak_claim_is_not_stronger_than_a_declared_key(tmp_path, monkeypatch):
    weak = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="gizmoid")}}, name="w")
    strong = build_snapshot(tmp_path, monkeypatch, {"pg": {"tables": _tables(key=["gizmoid"])}}, name="p")
    order = {"tentative": 0, "candidate": 0, "supported": 1, "declared": 2, "observed": 3}
    w, s = _status(weak), _status(strong)
    assert w and s
    assert order.get(w[0], 0) <= order.get(s[0], 0)
    assert "weak" in candidate_text(weak) and "weak" not in candidate_text(strong)


def test_weak_key_on_a_non_matching_column_proposes_nothing(tmp_path, monkeypatch):
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="label")}})
    assert candidates(snap) == []


def test_weak_key_does_not_make_other_columns_parents(tmp_path, monkeypatch):
    """Only the key column is claimed. A child named after the table's *other* column gets nothing."""
    tables = {
        "gizmo": {"fields": [("gizmoid", "integer"), ("ownercode", "integer")], "weak_key": "gizmoid"},
        "gizmoline": {"fields": [("id", "integer"), ("ownercode", "integer")]},
    }
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": tables}})
    assert all(row[3] != "ownercode" for row in candidates(snap))


def test_a_measured_non_unique_column_is_not_made_a_parent_by_a_sorting_key(tmp_path, monkeypatch):
    """RISK: a ClickHouse sorting key is not a uniqueness constraint. If the source itself measured
    the column as mostly duplicated, the weak claim should not propose it as a parent."""
    snap = build_snapshot(
        tmp_path, monkeypatch,
        {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="gizmoid", unique={"gizmoid": 0.2})}},
    )
    assert candidates(snap) == [], "measured 0.2 unique contradicts the weak claim"


def test_composite_sorting_key_is_not_a_single_column_claim(tmp_path, monkeypatch):
    """A two-column sorting key says nothing about either column alone being a key."""
    original = kit.write_artifact

    def with_composite(path, kind, tables_out, **kw):
        if kind == "collector-bundle":
            for row in tables_out["constraints.jsonl"]:
                if row.get("native_id", "").endswith(":sorting_key"):
                    row["field_names"] = ["gizmoid", "label"]
        return original(path, kind, tables_out, **kw)

    monkeypatch.setattr(kit, "write_artifact", with_composite)
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="gizmoid")}})
    assert candidates(snap) == []


def test_primary_key_constraint_name_is_also_a_weak_claim(tmp_path, monkeypatch):
    original = kit.write_artifact

    def as_primary_key(path, kind, tables_out, **kw):
        if kind == "collector-bundle":
            for row in tables_out["constraints.jsonl"]:
                if row.get("native_id", "").endswith(":sorting_key"):
                    row["native_id"] = row["native_id"].replace(":sorting_key", ":primary_key")
        return original(path, kind, tables_out, **kw)

    monkeypatch.setattr(kit, "write_artifact", as_primary_key)
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="gizmoid")}})
    assert ("gizmoline", "gizmoid", "gizmo", "gizmoid") in candidates(snap)
    assert "weak" in candidate_text(snap)


def test_an_unrelated_index_constraint_is_not_a_key_claim(tmp_path, monkeypatch):
    """Any other `index` constraint (e.g. a skipping index or a plain secondary index) must not
    be read as a key claim."""
    original = kit.write_artifact

    def as_plain_index(path, kind, tables_out, **kw):
        if kind == "collector-bundle":
            for row in tables_out["constraints.jsonl"]:
                if row.get("native_id", "").endswith(":sorting_key"):
                    row["native_id"] = "gizmo_idx_secondary"
        return original(path, kind, tables_out, **kw)

    monkeypatch.setattr(kit, "write_artifact", as_plain_index)
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": _tables(weak_key="gizmoid")}})
    assert candidates(snap) == []


def test_weak_parent_with_zero_sampled_overlap_is_dropped(tmp_path, monkeypatch):
    """A1 interplay: a weak key claim does not exempt the candidate from the measured-zero drop."""
    parent = {"fields": [("gizmoid", "integer")], "weak_key": "gizmoid", "values": {"gizmoid": list(range(100, 160))}}
    child = {"fields": [("id", "integer"), ("gizmoid", "integer")], "values": {"gizmoid": list(range(1000, 1060))}}
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": {"gizmo": parent, "gizmoline": child}}})
    assert candidates(snap) == []


def test_weak_parent_with_overlap_keeps_the_label(tmp_path, monkeypatch):
    parent = {"fields": [("gizmoid", "integer")], "weak_key": "gizmoid", "values": {"gizmoid": list(range(100, 160))}}
    child = {"fields": [("id", "integer"), ("gizmoid", "integer")], "values": {"gizmoid": list(range(100, 130))}}
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": {"gizmo": parent, "gizmoline": child}}})
    assert ("gizmoline", "gizmoid", "gizmo", "gizmoid") in candidates(snap)
    assert "weak" in candidate_text(snap)


@pytest.mark.parametrize("name", ["gizmoid", "GizmoID"])
def test_key_name_match_is_case_insensitive(tmp_path, monkeypatch, name):
    tables = {
        "gizmo": {"fields": [(name, "integer")], "weak_key": name},
        "gizmoline": {"fields": [("id", "integer"), ("gizmoid", "integer")]},
    }
    snap = build_snapshot(tmp_path, monkeypatch, {"ch": {"kind": "clickhouse", "tables": tables}})
    assert candidates(snap)


# --- collector side, through the existing fake ClickHouse client (no live server) ----------------
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from test_collectors import ChClient, config  # noqa: E402
from dbig_collectors.runtime import Budget, discover_clickhouse  # noqa: E402


def _claims(tables_row, columns_rows=None):
    class Client(ChClient):
        pass

    Client.tables_row = tables_row
    if columns_rows is not None:
        Client.columns_rows = columns_rows
    return discover_clickhouse(Client(), config("clickhouse"), Budget(2))


BASE = ("orders", "uuid-7", "MergeTree", "", "{sorting}", "{primary}", [], [], 0, "")


def _row(sorting, primary):
    row = list(BASE)
    row[4], row[5] = sorting, primary
    return tuple(row)


COLS = [("orders", "id", "UInt64", 1, 0, ""), ("orders", "ownerKey", "UInt64", 2, 0, "")]


def test_collector_sorting_and_primary_keys_on_different_columns_both_reported():
    got = _claims(_row("ownerKey", "id"), COLS)["constraints.jsonl"]
    assert sorted((c.native_id, tuple(c.field_names)) for c in got) == [
        ("uuid-7:primary_key", ("id",)), ("uuid-7:sorting_key", ("ownerKey",))]


def test_collector_identical_keys_are_reported_once():
    got = _claims(_row("id", "id"), COLS)["constraints.jsonl"]
    assert [c.native_id for c in got] == ["uuid-7:primary_key"]


@pytest.mark.parametrize("expr", ["`id`", " id ", "ID"])
def test_collector_quoting_whitespace_and_case(expr):
    got = _claims(_row(expr, expr), COLS)["constraints.jsonl"]
    assert len(got) in (0, 1)  # quoting/whitespace tolerated; a wrong-case name is not a known column
    if expr != "ID":
        assert got and got[0].field_names == ["id"]


@pytest.mark.parametrize("expr", ["", "tuple()", "(id)", "id, ownerKey", "cityHash64(id)", "nosuchcolumn", "id; DROP"])
def test_collector_skips_anything_but_one_known_plain_column_and_never_stores_the_expression(expr):
    records = _claims(_row(expr, expr), COLS)
    assert records["constraints.jsonl"] == []
    assert "cityHash64" not in str(records) and "DROP" not in str(records)


def test_collector_keeps_hashed_attributes_for_old_readers():
    obj = _claims(_row("id", "id"), COLS)["objects.jsonl"][0]
    assert obj.attributes.get("sort_key_sha256") and obj.attributes.get("primary_key_sha256")
