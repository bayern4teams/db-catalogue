"""DEC-1 / A2 (user, 2026-10-13): the parent column must resemble the child's name; the right
table's wrong column is never proposed."""

from __future__ import annotations

from catalogue_fixture_kit import build_snapshot, candidates


def _found(tmp_path, monkeypatch, tables):
    return candidates(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}))


def _location(locationid_uniqueness):
    return {
        "location": {"fields": [("companyid", "integer"), ("locationid", "integer")],
                     "unique": {"companyid": 1.0, "locationid": locationid_uniqueness}},
        "visit": {"fields": [("id", "integer"), ("locationid", "integer")]},
    }


def test_the_wrong_column_of_the_right_table_is_never_proposed(tmp_path, monkeypatch):
    """locationid measures 0.9826 (below the threshold), companyid 1.0: nothing is proposed."""
    assert _found(tmp_path, monkeypatch, _location(0.9826)) == []


def test_the_column_named_like_the_child_is_chosen_when_it_qualifies(tmp_path, monkeypatch):
    found = _found(tmp_path, monkeypatch, _location(1.0))
    assert found == [("visit", "locationid", "location", "locationid")], found


def test_a_generic_unique_column_loses_to_one_named_like_the_child(tmp_path, monkeypatch):
    found = _found(tmp_path, monkeypatch, {
        "vendor": {"fields": [("id", "integer"), ("vendor_id", "integer")],
                   "unique": {"id": 1.0, "vendor_id": 1.0}},
        "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")]},
    })
    assert found == [("shipment", "vendor_id", "vendor", "vendor_id")], found


def test_a_generic_parent_key_is_still_accepted(tmp_path, monkeypatch):
    found = _found(tmp_path, monkeypatch, {
        "vendor": {"fields": [("id", "integer")], "unique": {"id": 1.0}},
        "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")]},
    })
    assert found == [("shipment", "vendor_id", "vendor", "id")], found
