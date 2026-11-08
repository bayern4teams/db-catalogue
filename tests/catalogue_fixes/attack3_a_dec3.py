# ruff: noqa: E501 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack H1(a) for DEC-3 / D2(3): optional dataset groups or forbidden source pairs, default none.

Derived from engineer-fixes D2 point 3 ("let the operator declare dataset groups or forbidden
source pairs in configuration") and DEC-3 (default none, no behaviour change unless set). Run by path:
    uv run pytest tests/catalogue_fixes/attack3_a_dec3.py -q -p no:cacheprovider
"""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, candidates
from dbig_builder.config import BuilderConfig
from pydantic import ValidationError

PARENT = {"fields": [("rowkey", "integer"), ("klasscode", "text")], "unique": {"klasscode": 1.0}}
CHILD = {"fields": [("id", "integer"), ("klasscode", "text")]}


def _three(tmp_path, monkeypatch, **extra):
    sources = {
        "alpha": {"tables": {"gadgetkind": PARENT, "gadgetlocal": CHILD}},
        "beta": {"tables": {"gadgetpart": CHILD}},
        "gamma": {"tables": {"gadgetother": CHILD}},
    }
    return candidates(build_snapshot(tmp_path, monkeypatch, sources, builder_extra=extra or None))


def _sources(rows):
    return {r[0] for r in rows}


def test_default_none_is_unchanged_every_source_pairs(tmp_path, monkeypatch):
    rows = _three(tmp_path, monkeypatch)
    assert _sources(rows) == {"gadgetlocal", "gadgetpart", "gadgetother"}


def test_empty_config_values_equal_default(tmp_path, monkeypatch):
    a = _three(tmp_path, monkeypatch)
    b = candidates(build_snapshot(tmp_path, monkeypatch, {
        "alpha": {"tables": {"gadgetkind": PARENT, "gadgetlocal": CHILD}},
        "beta": {"tables": {"gadgetpart": CHILD}},
        "gamma": {"tables": {"gadgetother": CHILD}},
    }, name="b", builder_extra={"source_groups": [], "forbidden_source_pairs": []}))
    assert a == b


def test_forbidden_pair_removes_only_that_pair_and_never_same_source(tmp_path, monkeypatch):
    rows = _three(tmp_path, monkeypatch, forbidden_source_pairs=[("alpha", "beta")])
    assert _sources(rows) == {"gadgetlocal", "gadgetother"}


def test_forbidden_pair_is_symmetric(tmp_path, monkeypatch):
    rows = _three(tmp_path, monkeypatch, forbidden_source_pairs=[("beta", "alpha")])
    assert "gadgetpart" not in _sources(rows) and "gadgetlocal" in _sources(rows)


def test_groups_allow_within_and_forbid_across(tmp_path, monkeypatch):
    rows = _three(tmp_path, monkeypatch, source_groups=[["alpha", "beta"], ["gamma"]])
    assert _sources(rows) == {"gadgetlocal", "gadgetpart"}


def test_a_source_in_no_group_is_not_restricted(tmp_path, monkeypatch):
    rows = _three(tmp_path, monkeypatch, source_groups=[["alpha", "beta"]])
    assert _sources(rows) == {"gadgetlocal", "gadgetpart", "gadgetother"}


def test_a_self_pair_does_not_forbid_the_source_from_its_own_tables(tmp_path, monkeypatch):
    try:
        rows = _three(tmp_path, monkeypatch, forbidden_source_pairs=[("alpha", "alpha")])
    except ValidationError:
        return  # rejecting a self-pair is also acceptable
    assert "gadgetlocal" in _sources(rows)


def test_groups_that_isolate_everything_still_keep_same_source_links(tmp_path, monkeypatch):
    rows = _three(tmp_path, monkeypatch, source_groups=[["alpha"], ["beta"], ["gamma"]])
    assert _sources(rows) == {"gadgetlocal"}


@pytest.mark.parametrize("bad", [{"forbidden_source_pairs": [("a", "b", "c")]},
                                 {"forbidden_source_pairs": [("a",)]},
                                 {"source_groups": [[]]}])
def test_malformed_config_is_rejected_not_ignored(bad):
    with pytest.raises((ValidationError, ValueError)):
        BuilderConfig(signing_keys={"k": "KEY"}, **bad)


def test_a_group_listing_the_same_source_twice_is_handled():
    cfg = BuilderConfig(signing_keys={"k": "KEY"}, source_groups=[["a", "a"], ["b"]])
    assert cfg.pair_allowed("a", "a") is True and cfg.pair_allowed("a", "b") is False


def test_overlapping_groups_do_not_crash_and_are_symmetric():
    cfg = BuilderConfig(signing_keys={"k": "KEY"}, source_groups=[["a", "b"], ["b", "c"]])
    for x, y in [("a", "b"), ("b", "c"), ("a", "c")]:
        assert cfg.pair_allowed(x, y) == cfg.pair_allowed(y, x)


def test_config_from_a_file_accepts_the_options(tmp_path):
    """The CLI loads config from a file; the option must be reachable that way, not only in Python."""
    import json
    cfg = BuilderConfig.model_validate(json.loads(json.dumps(
        {"signing_keys": {"k": "KEY"}, "source_groups": [["a", "b"]], "forbidden_source_pairs": [["a", "c"]]})))
    assert cfg.pair_allowed("a", "c") is False and cfg.pair_allowed("a", "b") is True


def test_case_of_source_ids_is_literal(tmp_path, monkeypatch):
    """Source ids are exact strings; a differently cased id does not silently match (no surprise)."""
    rows = _three(tmp_path, monkeypatch, forbidden_source_pairs=[("ALPHA", "BETA")])
    assert "gadgetpart" in _sources(rows)


def test_forbidden_pair_also_removes_cross_source_composite_candidates(tmp_path, monkeypatch):
    """Composite-key candidates are a second candidate generator; the operator's rule must bind it
    too. Control first: with no config the composite candidate exists."""
    parent = {"fields": [("regionx", "integer"), ("slotx", "integer")], "key": ["regionx", "slotx"]}
    child = {"fields": [("id", "integer"), ("depot_regionx", "integer"), ("depot_slotx", "integer")]}
    sources = {"alpha": {"tables": {"depot": parent}}, "beta": {"tables": {"parcel": child}}}
    free = build_snapshot(tmp_path, monkeypatch, sources, name="free")
    assert any(r[0] == "parcel" for r in candidates(free)), "control: composite candidate expected"
    shut = build_snapshot(tmp_path, monkeypatch, sources, name="shut",
                          builder_extra={"forbidden_source_pairs": [("alpha", "beta")]})
    assert not any(r[0] == "parcel" for r in candidates(shut)), candidates(shut)
