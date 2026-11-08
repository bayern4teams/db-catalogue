# ruff: noqa: E501, PT006 # C5: reason=adversarial test file kept as its author wrote it; approved=user, 2026-11-08; review=2027-05-08
"""Attack H1(a) DEC-12 noise probe: names that are unique by accident, identical in two tables."""
import pytest
from catalogue_fixture_kit import build_snapshot, candidate_text, candidates


@pytest.mark.parametrize("col,typ", [("created_at", "timestamp"), ("email", "text"), ("title", "text"),
                                     ("slug", "text"), ("name", "text"), ("amount", "numeric"), ("url", "text"),
                                     ("description", "text"), ("label", "text"), ("value", "text"), ("type", "text"), ("status", "text")])
def test_accidentally_unique_ordinary_columns_do_not_become_references(tmp_path, monkeypatch, col, typ):
    t = {"gadgetkind": {"fields": [("rowkey", "integer"), (col, typ)], "unique": {col: 1.0}},
         "gadgetpart": {"fields": [("rowid", "integer"), (col, typ)]}}
    found = candidates(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": t}}))
    assert found == [], (found, candidate_text(build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": t}}, name="x"))[:0])
