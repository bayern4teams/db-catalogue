"""A1 + D2 + D9: a relationship is not proposed without saying what was checked (plan 9, Phase 2).

User decisions 2026-10-12:

1. Every candidate says what was *not* checked: evidence "value overlap not measured" with the
   reason (sampling off, no tokens, below minimum support, different key IDs), and the page says
   "values not compared".
2. With sampling on, a candidate whose measured containment is 0 of N, with N at or above the drop
   threshold (default `MIN_TOKEN_SUPPORT`), is dropped.
3. A non-zero overlap, even 21% to 27%, is shown with its number and sample size and never drops.
   A sample below the threshold keeps its candidate, marked "sample too small".

Mutation check for the attacker: remove the drop and `test_disjoint_columns_give_no_candidate`
must fail; remove the label and the `*_says_values_were_not_compared` tests must fail.

The threshold is imported (`MIN_TOKEN_SUPPORT`), never typed here, so a changed default is
followed by the tests; the drop's own config field name is not assumed.
"""

from __future__ import annotations

import json

import pytest
from catalogue_fixture_kit import (
    build_snapshot,
    candidate_text,
    candidates,
    containment_metrics,
    edge_kinds,
)
from dbig_builder.builder import MIN_TOKEN_SUPPORT
from dbig_builder.render import render_catalog
from dbig_contracts.artifact import read_artifact

N = MIN_TOKEN_SUPPORT  # the smallest sample at which 0 of N is evidence
CHILD = ("part", "gadget_id", "gadget", "id")


def _sources(
    child_values=None, parent_values=None, *, cross=True, child_key="run-1", parent_key="run-1"
):
    """A child `part.gadget_id` and a parent `gadget.id` (declared key). `None` values means no
    tokens at all (sampling off). `cross` puts the child in another source than the parent."""
    parent = {"fields": [("id", "integer"), ("title", "text")], "key": ["id"],
              "token_key": parent_key}
    child = {"fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"],
             "token_key": child_key}
    if parent_values is not None:
        parent["values"] = {"id": parent_values}
    if child_values is not None:
        child["values"] = {"gadget_id": child_values}
    if cross:
        return {"alpha": {"tables": {"gadget": parent}}, "beta": {"tables": {"part": child}}}
    return {"alpha": {"tables": {"gadget": parent, "part": child}}}


def _snapshot(tmp_path, monkeypatch, **kwargs):
    return build_snapshot(tmp_path, monkeypatch, _sources(**kwargs))


def _page(snapshot, tmp_path) -> str:
    out = tmp_path / "page.html"
    render_catalog(snapshot, [], out, None)
    return out.read_text(encoding="utf-8").lower()


def _range(start, count):
    return list(range(start, start + count))


# --- 1. what was not checked --------------------------------------------------------------------


@pytest.mark.parametrize("cross", [True, False], ids=["cross-source", "same-source"])
def test_with_sampling_off_the_candidate_exists_and_none_is_dropped(tmp_path, monkeypatch, cross):
    """Control: no tokens, no measurement, so nothing may be dropped on overlap."""
    found = candidates(_snapshot(tmp_path, monkeypatch, cross=cross))
    assert CHILD in found, found


@pytest.mark.parametrize("cross", [True, False], ids=["cross-source", "same-source"])
def test_sampling_off_says_values_were_not_compared(tmp_path, monkeypatch, cross):
    snapshot = _snapshot(tmp_path, monkeypatch, cross=cross)
    text = candidate_text(snapshot)
    assert "value overlap not measured" in text, text
    assert "sampling" in text or "no value tokens" in text, "the reason is stated"
    assert "values not compared" in _page(snapshot, tmp_path)


def test_different_key_ids_say_why_the_overlap_was_not_measured(tmp_path, monkeypatch):
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, N), parent_values=_range(0, N),
        child_key="run-1", parent_key="run-2",
    )
    assert CHILD in candidates(snapshot), "abstaining must not drop (control half)"
    assert containment_metrics(snapshot) == [], "no number from tokens that cannot be compared"
    text = candidate_text(snapshot)
    assert "value overlap not measured" in text and "key" in text, text
    assert "values not compared" in _page(snapshot, tmp_path)


def test_a_sample_below_the_threshold_keeps_its_candidate_marked_too_small(tmp_path, monkeypatch):
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, N - 1), parent_values=_range(1000, N),
    )
    assert CHILD in candidates(snapshot), "0 of N-1 is not evidence: the candidate stays"
    assert "sample too small" in candidate_text(snapshot)
    assert "sample too small" in _page(snapshot, tmp_path)


def test_a_sample_below_the_threshold_is_never_dropped(tmp_path, monkeypatch):
    """Control (passes before and after): the keep half of the line above, without the label."""
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, N - 1), parent_values=_range(1000, N),
    )
    assert CHILD in candidates(snapshot)


# --- 2. measured zero is dropped ----------------------------------------------------------------


@pytest.mark.parametrize("cross", [True, False], ids=["cross-source", "same-source"])
def test_disjoint_columns_give_no_candidate(tmp_path, monkeypatch, cross):
    """0 of N with N exactly at the threshold. Mutation target: removing the drop fails this."""
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, N), parent_values=_range(1000, N + 3),
        cross=cross,
    )
    assert candidates(snapshot) == []
    assert "REFERENCE_CANDIDATE" not in edge_kinds(snapshot)


def test_a_large_disjoint_sample_is_dropped_too(tmp_path, monkeypatch):
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, 10 * N), parent_values=_range(10_000, 10 * N)
    )
    assert candidates(snapshot) == []


def test_only_the_disjoint_candidate_is_dropped_not_its_neighbour(tmp_path, monkeypatch):
    """One parent, two children: `part` overlaps and stays, `crate` is disjoint and goes."""
    sources = _sources(child_values=_range(0, N), parent_values=_range(0, N + 2), cross=False)
    sources["alpha"]["tables"]["crate"] = {
        "fields": [("id", "integer"), ("gadget_id", "integer")], "key": ["id"],
        "values": {"gadget_id": _range(5000, N)},
    }
    snapshot = build_snapshot(tmp_path, monkeypatch, sources)
    assert candidates(snapshot) == [CHILD]


def test_disjoint_columns_with_sampling_off_are_not_dropped(tmp_path, monkeypatch):
    """Control: the same two columns, no tokens. Ignorance is not a counterexample."""
    assert CHILD in candidates(_snapshot(tmp_path, monkeypatch))


def test_a_declared_foreign_key_is_never_dropped_for_zero_overlap(tmp_path, monkeypatch):
    """Control: the drop is for inferred candidates. A relationship the source declares stays
    even if the sampled values do not overlap (the clone may be partial)."""
    sources = _sources(child_values=_range(0, N), parent_values=_range(1000, N), cross=False)
    sources["alpha"]["tables"]["part"]["foreign"] = [("gadget_id", "gadget", "id")]
    snapshot = build_snapshot(tmp_path, monkeypatch, sources)
    assert "DECLARES_FK" in edge_kinds(snapshot)


# --- 3. any overlap is shown, ranks and warns, and never drops ----------------------------------


def test_overlapping_columns_give_a_candidate_with_the_numbers(tmp_path, monkeypatch):
    """Control: containment is measured and cited today and must stay."""
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, N), parent_values=_range(0, N + 4)
    )
    assert candidates(snapshot) == [CHILD]
    (metrics,) = containment_metrics(snapshot)
    assert metrics["contained_fraction"] == 1.0
    assert metrics["shared_tokens"] == N and metrics["child_distinct_sampled"] == N


@pytest.mark.parametrize("cross", [True, False], ids=["cross-source", "same-source"])
def test_a_quarter_overlap_keeps_its_candidate_with_number_and_sample_size(
    tmp_path, monkeypatch, cross
):
    """Control, the D9 rule: a true relationship can overlap only 21% to 27% (partial clones).
    8 child values, 2 found among the parent's: 25%."""
    child = _range(0, 8)
    parent = _range(0, 2) + _range(1000, 12)
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=child, parent_values=parent, cross=cross
    )
    assert candidates(snapshot) == [CHILD], "a non-zero overlap never drops"
    (metrics,) = containment_metrics(snapshot)
    assert metrics["contained_fraction"] == 0.25
    assert metrics["child_distinct_sampled"] == 8 and metrics["shared_tokens"] == 2


def test_one_shared_value_in_a_large_sample_still_keeps_the_candidate(tmp_path, monkeypatch):
    """Control: the line between 'none' and 'some' is exactly zero found, not a percentage."""
    child = _range(0, 10 * N)
    parent = [0] + _range(50_000, 10 * N)
    snapshot = _snapshot(tmp_path, monkeypatch, child_values=child, parent_values=parent)
    assert candidates(snapshot) == [CHILD]


# --- the record of a drop and the snapshot's hygiene --------------------------------------------


@pytest.mark.parametrize("disjoint", [True, False], ids=["disjoint", "overlapping"])
def test_no_token_or_value_reaches_the_snapshot(tmp_path, monkeypatch, disjoint):
    """Control: whatever is recorded about a drop is counts only. No token digest and no sampled
    value may appear in any snapshot file, dropped or not."""
    child = _range(0, N)
    parent = _range(7000, N) if disjoint else _range(0, N)
    snapshot = _snapshot(tmp_path, monkeypatch, child_values=child, parent_values=parent)
    bundle_tokens = []
    for bundle in sorted(tmp_path.glob("bundle-*-snapshot")):
        for line in (bundle / "tokens.jsonl").read_text(encoding="utf-8").splitlines():
            bundle_tokens.extend(json.loads(line)["tokens"])
    assert bundle_tokens
    for path in sorted(snapshot.iterdir()):
        content = path.read_text(encoding="utf-8", errors="replace")
        for token in bundle_tokens:
            assert token not in content, f"a token leaked into {path.name}"


def test_the_snapshot_still_validates_after_assembling(tmp_path, monkeypatch):
    """Control: `read_artifact` verifies the manifest digests, so a diagnostic or evidence row added
    for a drop must be one the contracts accept (an additive change)."""
    snapshot = _snapshot(
        tmp_path, monkeypatch, child_values=_range(0, N), parent_values=_range(1000, N)
    )
    manifest, _ = read_artifact(snapshot)
    assert manifest.content_type == "catalog-snapshot"
