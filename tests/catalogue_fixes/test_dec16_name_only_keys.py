"""DEC-16 (user, 2026-10-14): a measured-unique parent whose name is not stem+id style needs
measured value overlap before its same-named child column becomes a candidate."""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, candidates
from dbig_builder.builder import MIN_TOKEN_SUPPORT

N = MIN_TOKEN_SUPPORT


def _tables(col, parent_values=None, child_values=None, typ="text"):
    parent = {"fields": [("rowkey", "integer"), (col, typ)], "unique": {col: 1.0},
              "token_key": "run-1"}
    child = {"fields": [("rowid", "integer"), (col, typ)], "token_key": "run-1"}
    if parent_values is not None:
        parent["values"] = {col: parent_values}
    if child_values is not None:
        child["values"] = {col: child_values}
    return {"alpha": {"tables": {"gadgetkind": parent, "gadgetpart": child}}}


def _found(tmp_path, monkeypatch, sources):
    return candidates(build_snapshot(tmp_path, monkeypatch, sources))


@pytest.mark.parametrize("col", ["email", "status", "created_at", "url"])
def test_unsampled_ordinary_names_propose_nothing(tmp_path, monkeypatch, col):
    assert _found(tmp_path, monkeypatch, _tables(col)) == []


def test_measured_overlap_no_longer_admits_the_pair(tmp_path, monkeypatch):
    # Flipped on purpose by P6-4 (owner decision 2026-10-19, DEC-20): an ordinary name links only
    # to a declared key; full measured overlap is not enough any more.
    values = [f"v{i}" for i in range(N + 3)]
    assert _found(tmp_path, monkeypatch, _tables("email", values, values)) == []


def test_a_sample_below_the_support_does_not_admit_the_pair(tmp_path, monkeypatch):
    values = [f"v{i}" for i in range(max(N - 2, 1))]
    assert _found(tmp_path, monkeypatch, _tables("email", values, values)) == []


def test_stem_plus_id_style_names_still_link_without_sampling(tmp_path, monkeypatch):
    sources = _tables("classificationid", typ="integer")
    found = _found(tmp_path, monkeypatch, sources)
    assert ("gadgetpart", "classificationid", "gadgetkind", "classificationid") in found, found


def test_a_weak_key_with_an_ordinary_name_needs_overlap_too(tmp_path, monkeypatch):
    # Live run 2026-10-17: `freeMails`, a ClickHouse sorting key, linked a same-named Mongo field.
    tables = {
        "contact": {"fields": [("rowid", "integer"), ("email", "text")], "weak_key": "email"},
        "staff": {"fields": [("rowid", "integer"), ("email", "text")]},
    }
    assert _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}) == []


def test_a_weak_key_with_a_reference_name_still_links_unsampled(tmp_path, monkeypatch):
    tables = {
        "contact": {
            "fields": [("rowid", "integer"), ("contactID", "text")],
            "weak_key": "contactID",
        },
        "staff": {"fields": [("rowid", "integer"), ("contactID", "text")]},
    }
    assert ("staff", "contactID", "contact", "contactID") in _found(
        tmp_path, monkeypatch, {"alpha": {"tables": tables}}
    )
