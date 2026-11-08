# ruff: noqa: E501, I001 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack H1(a) for DEC-12 / B5(X1): an identical, non-generic column name links a child to a
MEASURED-UNIQUE parent column (before: only to a declared key).

Derived from artifacts/engineer-fixes.md (B5 check: `companyindustry.classificationid` to
the report's own pair is proposed) and the DEC-12 text only. Invented names.
Not collected by default; run by path:
    uv run pytest tests/catalogue_fixes/attack3_a_dec12.py -q -p no:cacheprovider
"""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, candidate_text, candidates, token_set  # noqa: F401 # C5: reason=name imported but unused here, kept as the author wrote it; approved=user, 2026-11-08; review=2027-05-08


def _snap(tmp_path, monkeypatch, parent_fields, parent_unique, child_fields, *, name="s",
          parent_key=None, extra=None, sources=None):
    tables = {
        "gadgetkind": {"fields": parent_fields, "unique": parent_unique,
                       **({"key": parent_key} if parent_key else {})},
        "gadgetpart": {"fields": child_fields},
        **(extra or {}),
    }
    return build_snapshot(tmp_path, monkeypatch, sources or {"alpha": {"tables": tables}}, name=name)


KIND_FIELDS = [("rowkey", "integer"), ("klasscode", "text"), ("label", "text")]
PART_FIELDS = [("id", "integer"), ("klasscode", "text")]


def test_positive_measured_unique_parent_with_identical_name_links(tmp_path, monkeypatch):
    """The B5 check in synthetic form: names share no table-name stem, the parent is only measured."""
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": 1.0}, PART_FIELDS)
    assert ("gadgetpart", "klasscode", "gadgetkind", "klasscode") in candidates(snap)


@pytest.mark.parametrize("uniqueness", [0.5, 0.9, 0.98])
def test_negative_a_parent_below_the_measured_threshold_is_not_a_parent(tmp_path, monkeypatch, uniqueness):
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": uniqueness}, PART_FIELDS)
    assert candidates(snap) == []


def test_negative_no_statistic_and_no_declared_key_is_not_a_parent(tmp_path, monkeypatch):
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {}, PART_FIELDS)
    assert candidates(snap) == []


@pytest.mark.parametrize("generic", ["id", "key", "code", "uuid", "ref", "num", "no", "fk"])
def test_generic_names_never_link_by_identity(tmp_path, monkeypatch, generic):
    """`gadgetpart.<generic>` and a measured-unique `gadgetkind.<generic>` share only a bland word."""
    kind = [("rowkey", "integer"), (generic, "text")]
    part = [("rowid", "integer"), (generic, "text")]
    snap = _snap(tmp_path, monkeypatch, kind, {generic: 1.0}, part)
    assert candidates(snap) == [], f"generic column name {generic!r} linked by identity"


def test_type_incompatibility_still_blocks(tmp_path, monkeypatch):
    part = [("id", "integer"), ("klasscode", "boolean")]
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": 1.0}, part)
    assert candidates(snap) == []


def test_a_table_never_links_to_itself(tmp_path, monkeypatch):
    tables = {"gadgetkind": {"fields": [("rowkey", "integer"), ("klasscode", "text")],
                             "unique": {"klasscode": 1.0}}}
    snap = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    assert candidates(snap) == []


def test_the_parent_side_itself_is_not_a_child_of_its_child(tmp_path, monkeypatch):
    """Direction: the measured-unique side is the parent; the other (non-unique) side the child."""
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": 1.0}, PART_FIELDS)
    assert all(row[0] == "gadgetpart" and row[2] == "gadgetkind" for row in candidates(snap))


def test_two_measured_unique_parents_with_the_same_name_both_are_offered_not_one_silently(tmp_path, monkeypatch):
    """Tie: nothing in the Decision lets the builder pick one parent by order, so either both are
    offered or none; exactly one chosen by dict order would be an arbitrary claim."""
    extra = {"gadgetkind2": {"fields": KIND_FIELDS, "unique": {"klasscode": 1.0}}}
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": 1.0}, PART_FIELDS, extra=extra)
    parents = {row[2] for row in candidates(snap)}
    assert parents in ({"gadgetkind", "gadgetkind2"}, set()), parents


def test_evidence_says_the_parent_was_only_measured_not_declared(tmp_path, monkeypatch):
    """B5 proposal: 'make the 0.99 threshold and the key claim visible in the evidence'."""
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": 1.0}, PART_FIELDS)
    text = candidate_text(snap)
    assert "unique" in text, text
    assert "declared" not in text.replace("not declared", "").replace("undeclared", ""), (
        "a measured parent must not be described as declared")


def test_declared_key_parent_still_links(tmp_path, monkeypatch):
    """Regression: the old (declared) path is unchanged."""
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {}, PART_FIELDS, parent_key=["klasscode"])
    assert ("gadgetpart", "klasscode", "gadgetkind", "klasscode") in candidates(snap)


def test_case_differences_do_not_hide_the_identity(tmp_path, monkeypatch):
    part = [("id", "integer"), ("KlassCode", "text")]
    snap = _snap(tmp_path, monkeypatch, KIND_FIELDS, {"klasscode": 1.0}, part)
    assert candidates(snap) != []


def test_empty_catalogue_and_a_table_without_fields_do_not_crash(tmp_path, monkeypatch):
    tables = {"gadgetkind": {"fields": []}, "gadgetpart": {"fields": [("id", "integer")]}}
    snap = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    assert candidates(snap) == []


def test_cross_source_identity_link_is_labelled_values_not_compared(tmp_path, monkeypatch):
    """A cross-source measured-unique identity link is allowed (default config) but, unsampled, must
    say values were not compared (A1/D2 decision of 2026-10-12)."""
    sources = {
        "alpha": {"tables": {"gadgetkind": {"fields": KIND_FIELDS, "unique": {"klasscode": 1.0}}}},
        "beta": {"tables": {"gadgetpart": {"fields": PART_FIELDS}}},
    }
    snap = build_snapshot(tmp_path, monkeypatch, sources)
    assert ("gadgetpart", "klasscode", "gadgetkind", "klasscode") in candidates(snap)
    assert "not compared" in candidate_text(snap) or "not measured" in candidate_text(snap)


def test_sampling_on_zero_overlap_drops_the_identity_candidate(tmp_path, monkeypatch):
    """With sampling on, the A1 guard still applies to the new rule."""
    kind = {"fields": KIND_FIELDS, "unique": {"klasscode": 1.0},
            "values": {"klasscode": [f"a{i}" for i in range(60)]}}
    part = {"fields": PART_FIELDS, "values": {"klasscode": [f"b{i}" for i in range(60)]}}
    sources = {"alpha": {"tables": {"gadgetkind": kind}}, "beta": {"tables": {"gadgetpart": part}}}
    snap = build_snapshot(tmp_path, monkeypatch, sources)
    assert candidates(snap) == []


def test_sampling_on_full_overlap_keeps_the_identity_candidate(tmp_path, monkeypatch):
    shared = [f"a{i}" for i in range(60)]
    kind = {"fields": KIND_FIELDS, "unique": {"klasscode": 1.0}, "values": {"klasscode": shared}}
    part = {"fields": PART_FIELDS, "values": {"klasscode": shared[:40]}}
    sources = {"alpha": {"tables": {"gadgetkind": kind}}, "beta": {"tables": {"gadgetpart": part}}}
    snap = build_snapshot(tmp_path, monkeypatch, sources)
    assert ("gadgetpart", "klasscode", "gadgetkind", "klasscode") in candidates(snap)
