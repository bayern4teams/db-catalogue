# ruff: noqa
"""Attack H1(a) for DEC-1 / A2: the parent column must be the one whose name resembles the child's.

Derived from engineer-fixes A2 (check: with those two tables the candidate must name `locationid`,
or no candidate must be proposed) and DEC-1: "prefer the parent column whose name matches the
child's, and propose nothing if none resembles it". Invented names. Run by path:
    uv run pytest tests/catalogue_fixes/attack3_a_dec1.py -q -p no:cacheprovider
"""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, candidates


def _run(tmp_path, monkeypatch, parent_fields, unique, child_column, *, key=None, name="s", child_type="integer"):
    tables = {
        "depot": {"fields": parent_fields, "unique": unique, **({"key": key} if key else {})},
        "parcel": {"fields": [("rowid", "integer"), (child_column, child_type)]},
    }
    snap = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name=name)
    return [row for row in candidates(snap) if row[0] == "parcel"]


A2_FIELDS = [("depotid", "integer"), ("ownerid", "integer"), ("label", "text")]


def test_a2_the_report_case_names_the_matching_column_or_nothing(tmp_path, monkeypatch):
    """depot.depotid measures 0.9826 (below 0.99); depot.ownerid measures 1.0. Child parcel.depotid."""
    found = _run(tmp_path, monkeypatch, A2_FIELDS, {"depotid": 0.9826, "ownerid": 1.0}, "depotid")
    assert all(row[3] == "depotid" for row in found), found
    assert not any(row[3] == "ownerid" for row in found), "the wrong column of the right table was proposed"


def test_a2_when_both_qualify_the_named_one_wins_and_only_one_is_offered(tmp_path, monkeypatch):
    found = _run(tmp_path, monkeypatch, A2_FIELDS, {"depotid": 1.0, "ownerid": 1.0}, "depotid")
    assert [row[3] for row in found] == ["depotid"], found


def test_a2_the_wrong_column_alone_is_never_accepted(tmp_path, monkeypatch):
    """Only depot.ownerid qualifies; the child column is depotid: nothing may be proposed."""
    found = _run(tmp_path, monkeypatch, A2_FIELDS, {"ownerid": 1.0}, "depotid")
    assert found == [], found


@pytest.mark.parametrize("unrelated", ["zzzref", "alphabet", "qid"])
def test_a2_unrelated_unique_column_names_are_rejected(tmp_path, monkeypatch, unrelated):
    fields = [("depotid", "integer"), (unrelated, "integer")]
    found = _run(tmp_path, monkeypatch, fields, {unrelated: 1.0}, "depotid")
    assert found == [], found


def test_regression_conventional_child_to_table_id_still_links(tmp_path, monkeypatch):
    """customer_id -> customers.id (measured unique, undeclared) is the classic link; DEC-1 must not
    kill it just because `id` is a generic name (the child names the TABLE)."""
    fields = [("id", "integer"), ("label", "text")]
    found = _run(tmp_path, monkeypatch, fields, {"id": 1.0}, "depot_id")
    assert [(r[1], r[2], r[3]) for r in found] == [("depot_id", "depot", "id")], found


def test_regression_declared_key_with_a_different_name_still_links(tmp_path, monkeypatch):
    """The gate applies to non-declared parents only (DEC-1 text is about unique-column ties)."""
    fields = [("pk", "integer"), ("label", "text")]
    found = _run(tmp_path, monkeypatch, fields, {}, "depot_id", key=["pk"])
    assert [(r[1], r[2], r[3]) for r in found] == [("depot_id", "depot", "pk")], found


def test_a2_generic_unique_column_loses_to_a_named_one(tmp_path, monkeypatch):
    fields = [("id", "integer"), ("depotid", "integer")]
    found = _run(tmp_path, monkeypatch, fields, {"id": 1.0, "depotid": 1.0}, "depotid")
    assert [row[3] for row in found] == ["depotid"], found


def test_a2_generic_unique_column_when_the_named_one_is_below_threshold(tmp_path, monkeypatch):
    """The named column measures 0.98 (not unique enough); `id` is unique. For child `depotid` the
    `id` column does not resemble it -> nothing, or at most the table-name+id convention, never `ownerid`."""
    fields = [("id", "integer"), ("depotid", "integer")]
    found = _run(tmp_path, monkeypatch, fields, {"id": 1.0, "depotid": 0.98}, "depotid")
    assert all(row[3] in {"id", "depotid"} for row in found)


def test_a2_one_candidate_per_child_and_parent_table(tmp_path, monkeypatch):
    """Ties never produce several parent columns of one table for one child column."""
    fields = [("depotid", "integer"), ("depot_id", "integer"), ("depotkey", "integer")]
    found = _run(tmp_path, monkeypatch, fields, {n: 1.0 for n, _ in fields}, "depotid")
    assert len({(r[1], r[2]) for r in found}) == len(found), found


def test_a2_ties_are_deterministic(tmp_path, monkeypatch):
    fields = [("depotid", "integer"), ("depot_id", "integer")]
    one = _run(tmp_path, monkeypatch, fields, {n: 1.0 for n, _ in fields}, "depotid", name="a")
    two = _run(tmp_path, monkeypatch, list(reversed(fields)), {n: 1.0 for n, _ in fields}, "depotid", name="b")
    assert one == two


def test_a2_case_and_separator_differences_do_not_hide_the_resemblance(tmp_path, monkeypatch):
    fields = [("Depot_ID", "integer"), ("OwnerID", "integer")]
    found = _run(tmp_path, monkeypatch, fields, {"Depot_ID": 1.0, "OwnerID": 1.0}, "depotid")
    assert [row[3] for row in found] == ["Depot_ID"], found


def test_a2_composite_declared_key_gives_no_single_column_parent(tmp_path, monkeypatch):
    fields = [("depotid", "integer"), ("ownerid", "integer")]
    found = _run(tmp_path, monkeypatch, fields, {}, "depotid", key=["depotid", "ownerid"])
    assert all(row[3] != "ownerid" for row in found), found


def test_a2_empty_parent_table_and_no_fields_do_not_crash(tmp_path, monkeypatch):
    assert _run(tmp_path, monkeypatch, [("label", "text")], {}, "depotid") == []
