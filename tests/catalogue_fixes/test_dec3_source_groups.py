"""DEC-3 (user, 2026-10-13): optional dataset groups and forbidden source pairs, default none."""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, candidates
from dbig_builder.config import BuilderConfig


def _cfg(**extra):
    return BuilderConfig(signing_keys={"k": "KEY"}, **extra)


@pytest.mark.parametrize(
    ("kwargs", "left", "right", "allowed"),
    [
        ({}, "a", "b", True),
        ({"forbidden_source_pairs": [("a", "b")]}, "b", "a", False),
        ({"forbidden_source_pairs": [("a", "b")]}, "a", "c", True),
        ({"source_groups": [["a", "b"], ["c"]]}, "a", "c", False),
        ({"source_groups": [["a", "b"], ["c"]]}, "a", "b", True),
        ({"source_groups": [["a", "b"], ["c"]]}, "a", "d", True),
        ({"source_groups": [["a"]]}, "a", "a", True),
    ],
)
def test_pair_rules(kwargs, left, right, allowed):
    assert _cfg(**kwargs).pair_allowed(left, right) is allowed


def test_an_empty_group_is_rejected():
    with pytest.raises(ValueError, match=r"source group needs at least one non-empty source ID"):
        _cfg(source_groups=[[]])


TABLES = {
    "alpha": {"tables": {"vendor": {"fields": [("vendor_id", "integer")], "key": ["vendor_id"]}}},
    "beta": {"tables": {"shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")]}}},
}


def test_default_config_pairs_across_sources(tmp_path, monkeypatch):
    found = candidates(build_snapshot(tmp_path, monkeypatch, TABLES))
    assert ("shipment", "vendor_id", "vendor", "vendor_id") in found


def test_a_forbidden_pair_removes_the_cross_source_candidate(tmp_path, monkeypatch):
    snapshot = build_snapshot(
        tmp_path, monkeypatch, TABLES, builder_extra={"forbidden_source_pairs": [("alpha", "beta")]}
    )
    assert candidates(snapshot) == []
