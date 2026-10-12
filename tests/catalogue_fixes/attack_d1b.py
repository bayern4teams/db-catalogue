# ruff: noqa: E501
"""Attack tests for D1b (empty statistic cells say why) beyond test_d1b_empty_cells.py.

Run by file name:

    uv run pytest tests/catalogue_fixes/attack_d1b.py -p no:cacheprovider

The reason must follow the profile's method and values, never the source kind, the table or
the column name, and the page must stay well formed and escaped. Names are invented.
"""

from __future__ import annotations

import re

import pytest
from catalogue_fixture_kit import build_snapshot, bundle_dirs
from dbig_builder.config import BuilderConfig
from dbig_builder.render import render_catalog

NULL_PCT, DISTINCT, UNIQUENESS, AVG_BYTES = range(4)
ALL_NULL = "all values null"
NOT_PROVIDED = "not provided by this source"
NOT_COMPUTED = "not computed"


def _page(tmp_path, monkeypatch, sources, name="p"):
    snapshot = build_snapshot(tmp_path, monkeypatch, sources, name=name)
    out = tmp_path / f"{name}.html"
    render_catalog(
        snapshot, bundle_dirs(tmp_path, name), out,
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    return out.read_text(encoding="utf-8")


def cells(html: str, field: str) -> list[str]:
    row = re.search(
        rf"<tr><td class='num muted'>[^<]*</td><td><code>{re.escape(field)}</code>.*?</tr>", html
    )
    assert row, f"no row for {field}"
    return [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td class='num'>(.*?)</td>", row.group(0))]


def _one(kind, field, profile, *, table="gadget", table_kind="table"):
    return {"src": {"kind": kind, "tables": {table: {"kind": table_kind, "fields": [
        (field, "text"), ("pad", "text")], "profiles": {field: profile}}}}}


@pytest.mark.parametrize("kind", ["postgresql", "clickhouse", "mongodb"])
def test_the_same_profile_gives_the_same_reason_on_every_source_kind(tmp_path, monkeypatch, kind):
    profile = {"method": "source_statistic", "null_fraction": 1.0}
    html = _page(
        tmp_path, monkeypatch,
        _one(kind, "memo", profile, table_kind="collection" if kind == "mongodb" else "table"),
        name=kind,
    )
    found = cells(html, "memo")
    assert ALL_NULL in found[DISTINCT].lower() and ALL_NULL in found[UNIQUENESS].lower(), found
    assert NOT_PROVIDED in found[AVG_BYTES].lower(), found


@pytest.mark.parametrize(
    "field", ["is_null", "all_null", "null_fraction", "not_computed", "not provided", "100%", "0"]
)
def test_the_reason_ignores_a_column_name_that_names_a_reason(tmp_path, monkeypatch, field):
    html = _page(
        tmp_path, monkeypatch,
        _one("postgresql", field, {"method": "source_statistic", "null_fraction": 0.25}),
        name="n" + re.sub(r"\W", "", field),
    )
    found = cells(html, field)
    assert found[NULL_PCT] == "25.0%"
    assert NOT_PROVIDED in found[DISTINCT].lower() and ALL_NULL not in " ".join(found).lower()


def test_a_nearly_all_null_column_is_not_called_all_null(tmp_path, monkeypatch):
    html = _page(
        tmp_path, monkeypatch,
        _one("postgresql", "memo", {"method": "source_statistic", "null_fraction": 0.9999}),
    )
    found = cells(html, "memo")
    assert ALL_NULL not in " ".join(found).lower(), found
    assert NOT_PROVIDED in found[DISTINCT].lower()


def test_a_zero_is_a_number_not_an_empty_cell(tmp_path, monkeypatch):
    html = _page(
        tmp_path, monkeypatch,
        _one("postgresql", "memo", {
            "method": "source_statistic", "null_fraction": 0.0, "distinct_estimate": 0,
            "uniqueness": 0.0, "average_width_bytes": 0,
        }),
    )
    found = cells(html, "memo")
    assert found == ["0.0%", "0", "0.0%", "0"], found


def test_an_all_null_column_that_still_reports_a_distinct_count_shows_the_number(
    tmp_path, monkeypatch
):
    html = _page(
        tmp_path, monkeypatch,
        _one("postgresql", "memo", {
            "method": "source_statistic", "null_fraction": 1.0, "distinct_estimate": 3,
        }),
    )
    found = cells(html, "memo")
    assert found[DISTINCT] == "3" and ALL_NULL in found[UNIQUENESS].lower(), found


def test_a_profile_with_no_numbers_at_all_says_not_provided_in_every_cell(tmp_path, monkeypatch):
    html = _page(
        tmp_path, monkeypatch, _one("postgresql", "memo", {"method": "source_statistic"})
    )
    assert all(NOT_PROVIDED in c.lower() for c in cells(html, "memo")), cells(html, "memo")


def test_two_profiles_of_one_field_are_merged_before_the_reason_is_chosen(tmp_path, monkeypatch):
    sources = {"src": {"kind": "postgresql", "tables": {"gadget": {"fields": [("memo", "text")],
        "profiles": {"memo": {"method": "sample", "sample_count": 10, "null_fraction": 1.0}},
        "unique": {"memo": 0.5}}}}}
    html = _page(tmp_path, monkeypatch, sources)
    found = cells(html, "memo")
    assert found[NULL_PCT] == "100.0%" and found[UNIQUENESS] == "50.0%", found
    assert ALL_NULL in found[DISTINCT].lower(), found


def test_a_hostile_column_name_cannot_inject_through_the_new_cell(tmp_path, monkeypatch):
    name = "<script>x</script>"
    html = _page(tmp_path, monkeypatch, _one("postgresql", name, {"method": "source_statistic"}))
    assert "<script>x</script>" not in html


def test_every_card_row_has_exactly_four_numeric_cells_after_the_change(tmp_path, monkeypatch):
    sources = {
        "a": {"kind": "postgresql", "tables": {"t": {"fields": [("x", "text"), ("y", "text")],
              "profiles": {"x": {"method": "source_statistic", "null_fraction": 1.0}}}}},
        "b": {"kind": "clickhouse", "tables": {"t": {"fields": [("z", "text")]}}},
    }
    html = _page(tmp_path, monkeypatch, sources)
    for field in ("x", "y", "z"):
        assert len(cells(html, field)) == 4
    assert "<td class='num'></td>" not in html
    assert NOT_COMPUTED in cells(html, "z")[0].lower()
