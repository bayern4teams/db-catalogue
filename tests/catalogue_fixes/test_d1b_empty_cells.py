"""D1b: an empty statistic cell says why (plan section 9, Phase 1; `statistics-analysis.md`).

A blank cell becomes one of "not provided by this source", "all values null" or "not computed",
chosen from the profile's method and values, never from a source or column name. Acceptance:
fixtures for the three source kinds show the right reason in each empty cell and no blank cell
remains; a populated cell still shows its number.

Cell order in a column row: Null %, Distinct (est.), Uniqueness, Avg bytes.
"""

from __future__ import annotations

import re

import pytest
from catalogue_fixture_kit import build_snapshot, bundle_dirs
from dbig_builder.config import BuilderConfig
from dbig_builder.render import render_catalog

NULL_PCT, DISTINCT, UNIQUENESS, AVG_BYTES = range(4)


def _sources(pg_blank="memo", ch_blank="payload"):
    return {
        "pgsrc": {"kind": "postgresql", "tables": {"ledger": {"fields": [
            ("amount", "numeric"), (pg_blank, "text"),
        ], "profiles": {
            "amount": {"method": "source_statistic", "null_fraction": 0.1,
                       "distinct_estimate": 50, "uniqueness": 0.5, "average_width_bytes": 8},
            # a column that is all null: PostgreSQL reports no distinct estimate for it
            pg_blank: {"method": "source_statistic", "null_fraction": 1.0,
                       "average_width_bytes": 4},
        }}}},
        "chsrc": {"kind": "clickhouse", "tables": {"event": {"fields": [
            (ch_blank, "String"), ("flag", "UInt8"),
        ], "profiles": {
            # the only number ClickHouse yields: bytes per row
            ch_blank: {"method": "source_statistic", "average_width_bytes": 12},
        }}}},
        "mgsrc": {"kind": "mongodb", "tables": {"doc": {"kind": "collection", "fields": [
            ("title", "string"),
        ], "profiles": {
            "title": {"method": "sample", "sample_count": 100, "null_fraction": 0.0},
        }}}},
    }


@pytest.fixture
def page(tmp_path, monkeypatch):
    snapshot = build_snapshot(tmp_path, monkeypatch, _sources())
    out = tmp_path / "page.html"
    render_catalog(
        snapshot, bundle_dirs(tmp_path), out, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"})
    )
    return out.read_text(encoding="utf-8")


def cells(html: str, field: str) -> list[str]:
    row = re.search(rf"<tr><td class='num muted'>[^<]*</td><td><code>{field}</code>.*?</tr>", html)
    assert row, f"no row for {field}"
    return [re.sub(r"<[^>]+>", "", cell).strip()
            for cell in re.findall(r"<td class='num'>(.*?)</td>", row.group(0))]


def test_the_fixture_renders_all_four_cells_for_every_field(page):
    """Control: the harness reads four numeric cells per column row."""
    for field in ("amount", "memo", "payload", "flag", "title"):
        assert len(cells(page, field)) == 4, field


def test_a_populated_cell_still_shows_its_number(page):
    """Control: only empty cells change."""
    amount = cells(page, "amount")
    assert amount[NULL_PCT] and amount[DISTINCT] and amount[UNIQUENESS] and amount[AVG_BYTES]
    assert not any("not " in cell.lower() for cell in amount), amount
    assert cells(page, "memo")[NULL_PCT] and cells(page, "memo")[AVG_BYTES]
    assert cells(page, "payload")[AVG_BYTES]


def test_no_blank_statistic_cell_remains(page):
    assert "<td class='num'></td>" not in page
    for field in ("amount", "memo", "payload", "flag", "title"):
        assert all(cells(page, field)), (field, cells(page, field))


def test_an_all_null_column_says_all_values_null(page):
    found = cells(page, "memo")
    assert "all values null" in found[DISTINCT].lower(), found
    assert "all values null" in found[UNIQUENESS].lower(), found


def test_a_source_that_gives_only_a_width_says_not_provided_by_this_source(page):
    found = cells(page, "payload")
    for index in (NULL_PCT, DISTINCT, UNIQUENESS):
        assert "not provided by this source" in found[index].lower(), found


def test_a_sampled_field_says_not_provided_for_what_a_sample_does_not_compute(page):
    found = cells(page, "title")
    for index in (DISTINCT, UNIQUENESS, AVG_BYTES):
        assert "not provided by this source" in found[index].lower(), found


def test_a_field_with_no_profile_says_not_computed(page):
    found = cells(page, "flag")
    assert all("not computed" in cell.lower() for cell in found), found


def test_the_reason_follows_the_values_not_the_names(tmp_path, monkeypatch):
    """The same profiles under names that tempt a source- or column-based rule give the same
    reasons: a PostgreSQL column named like an engine, a ClickHouse column named `is_null`."""
    snapshot = build_snapshot(
        tmp_path, monkeypatch, _sources(pg_blank="clickhouse_all_null", ch_blank="is_null")
    )
    out = tmp_path / "renamed.html"
    render_catalog(
        snapshot, bundle_dirs(tmp_path), out, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"})
    )
    html = out.read_text(encoding="utf-8")
    assert "all values null" in cells(html, "clickhouse_all_null")[DISTINCT].lower()
    assert "not provided by this source" in cells(html, "is_null")[DISTINCT].lower()
