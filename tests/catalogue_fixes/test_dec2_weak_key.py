"""DEC-2 (user, 2026-10-13): a ClickHouse one-column sorting or primary key is a weak, labelled
parent claim. Names only, never values."""

from __future__ import annotations

from catalogue_fixture_kit import build_snapshot, candidate_text, candidates


def _tables(**vendor):
    return {
        "vendor": {"fields": [("vendor_id", "integer"), ("label", "text")], **vendor},
        "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")]},
    }


def test_a_table_with_no_key_and_no_statistics_is_not_a_parent(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": _tables()}})
    assert candidates(snapshot) == []


def test_a_one_column_sorting_key_makes_a_labelled_weak_parent(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": _tables(weak_key="vendor_id")}}
    )
    assert ("shipment", "vendor_id", "vendor", "vendor_id") in candidates(snapshot)
    text = candidate_text(snapshot)
    assert "weak key claim" in text and "uniqueness unproven" in text


def test_a_weak_key_on_a_different_column_proposes_nothing(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path, monkeypatch, {"alpha": {"tables": _tables(weak_key="label")}}
    )
    assert candidates(snapshot) == []
