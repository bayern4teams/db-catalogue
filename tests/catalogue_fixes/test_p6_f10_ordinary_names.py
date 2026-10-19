"""P6-4 (live finding F10, owner decision DEC-20): ordinary names need a declared key, and ties
among equal scores are broken by evidence, not by hash order.

Derived from plan section 9 Phase 6 "P6-4", `LEAD_REVIEW.md` rows F10 and "F10: new tie-break,
and C", and `HANDOFF.md` DEC-20, not from the implementation.

(a) A parent that is not a declared key and whose name is not reference-style no longer links by
    name, with or without measured overlap. A weak ClickHouse key claim counts as not declared.
(b) Among equal scores the order is: same source before cross-source; then declared key, then
    weak key claim, then measured uniqueness; then parent id.

Every behaviour that changes is `xfail(strict=True)` until P6-4 is built; the lead removes the
marks. The `control_*` tests pass before and after and guard against an over-fix (a rule that
drops declared keys or reference-style names, or one that lets the score lose to the source).

Tie scenarios do not depend on luck: field ids are
`stable_id(source, "field", "<table number>:<ordinal>")`, so each test computes the ids it
plants and asserts the premise ("the weak parent sorts 6th by id") before asserting the outcome.
Names are invented and generic (plan section 2.4).
"""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, builder_run, candidates
from dbig_builder.builder import MIN_TOKEN_SUPPORT
from dbig_contracts.ids import stable_id

N = MIN_TOKEN_SUPPORT
COL = "widget_id"
ORDINARY = ["description", "email", "run_timestamp", "snippet_text"]


# --- helpers ------------------------------------------------------------------------------------


def _found(tmp_path, monkeypatch, sources, **extra):
    return candidates(
        build_snapshot(tmp_path, monkeypatch, sources, builder_extra=extra or None)
    )


def _values(count=N + 3):
    return [f"v{i}" for i in range(count)]


def _ordinary_pair(col, *, parent_role="measured", sampled=True, cross=False):
    """A parent table `parentt` and a child table `childt`, both holding `col`."""
    parent = {"fields": [("rowkey", "integer"), (col, "text")]}
    if parent_role == "measured":
        parent["unique"] = {col: 1.0}
    elif parent_role == "weak":
        parent["weak_key"] = col
    elif parent_role == "declared":
        parent["key"] = [col]
    child = {"fields": [("rowid", "integer"), (col, "text")]}
    if sampled:
        parent["values"] = {col: _values()}
        child["values"] = {col: _values()}
    if cross:
        return {
            "alpha": {"tables": {"parentt": parent}},
            "beta": {"tables": {"childt": child}},
        }
    return {"alpha": {"tables": {"parentt": parent, "childt": child}}}


def _fid(source, table_number, ordinal=1):
    return stable_id(source, "field", f"{table_number}:{ordinal}")


def _role_table(role, col=COL):
    table = {"fields": [(col, "integer")]}
    if role == "declared":
        table["key"] = [col]
    elif role == "weak":
        table["weak_key"] = col
    elif role == "measured":
        table["unique"] = {col: 1.0}
    else:  # pragma: no cover - a typo in a test
        raise ValueError(role)
    return table


def _child_table():
    return {"fields": [("rowid", "integer"), (COL, "integer")]}


def _kept_parents(found):
    """Parent table names the child table `shipment` was linked to."""
    return {parent for child, _, parent, _ in found if child == "shipment"}


# --- (a) ordinary names ------------------------------------------------------------------------


@pytest.mark.parametrize("col", ORDINARY)
def test_full_overlap_no_longer_admits_an_ordinary_name_on_a_measured_unique_parent(
    tmp_path, monkeypatch, col
):
    """The live case: `description` is unique across a whole collection and 100% shared."""
    assert _found(tmp_path, monkeypatch, _ordinary_pair(col)) == []


def test_full_overlap_across_sources_does_not_admit_an_ordinary_name(tmp_path, monkeypatch):
    found = _found(tmp_path, monkeypatch, _ordinary_pair("description", cross=True))
    assert found == []


@pytest.mark.parametrize("col", ORDINARY)
def test_a_weak_key_claim_counts_as_not_declared_even_with_full_overlap(
    tmp_path, monkeypatch, col
):
    """`freeMails` in the live run: a ClickHouse sorting key with an ordinary name."""
    found = _found(tmp_path, monkeypatch, _ordinary_pair(col, parent_role="weak"))
    assert found == []


def test_a_declared_and_a_measured_parent_of_one_ordinary_name_link_only_the_declared(
    tmp_path, monkeypatch
):
    tables = {
        "declaredt": {"fields": [("rowkey", "integer"), ("email", "text")], "key": ["email"]},
        "measuredt": {"fields": [("rowkey", "integer"), ("email", "text")],
                      "unique": {"email": 1.0}, "values": {"email": _values()}},
        "shipment": {"fields": [("rowid", "integer"), ("email", "text")],
                     "values": {"email": _values()}},
    }
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    assert _kept_parents(found) == {"declaredt"}, found


def test_an_ordinary_name_is_dropped_beside_a_reference_style_name_that_links(
    tmp_path, monkeypatch
):
    tables = {
        "parentt": {
            "fields": [("rowkey", "integer"), ("description", "text"), ("widgetid", "integer")],
            "unique": {"description": 1.0, "widgetid": 1.0},
            "values": {"description": _values()},
        },
        "childt": {
            "fields": [("rowid", "integer"), ("description", "text"), ("widgetid", "integer")],
            "values": {"description": _values()},
        },
    }
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}})
    assert found == [("childt", "widgetid", "parentt", "widgetid")], found


def test_control_an_ordinary_name_on_a_measured_unique_parent_without_sampling_links_nothing(
    tmp_path, monkeypatch
):
    assert _found(tmp_path, monkeypatch, _ordinary_pair("description", sampled=False)) == []


@pytest.mark.parametrize("sampled", [False, True])
@pytest.mark.parametrize("col", ["description", "email"])
def test_control_a_declared_key_with_an_ordinary_name_still_links(
    tmp_path, monkeypatch, col, sampled
):
    found = _found(
        tmp_path, monkeypatch, _ordinary_pair(col, parent_role="declared", sampled=sampled)
    )
    assert found == [("childt", col, "parentt", col)], found


def test_control_a_declared_key_with_an_ordinary_name_links_across_sources(
    tmp_path, monkeypatch
):
    found = _found(
        tmp_path, monkeypatch, _ordinary_pair("description", parent_role="declared", cross=True)
    )
    assert found == [("childt", "description", "parentt", "description")], found


@pytest.mark.parametrize("col", ["widgetid", "widget_id", "widgetID", "WidgetId"])
@pytest.mark.parametrize("sampled", [False, True])
def test_control_a_reference_style_name_on_a_measured_unique_parent_still_links(
    tmp_path, monkeypatch, col, sampled
):
    """Reference-style names keep linking without a declared key (the parent is measured unique)."""
    parent = {"fields": [("rowkey", "integer"), (col, "integer")], "unique": {col: 1.0}}
    child = {"fields": [("rowid", "integer"), (col, "integer")]}
    if sampled:
        parent["values"] = {col: _values()}
        child["values"] = {col: _values()}
    found = _found(
        tmp_path, monkeypatch, {"alpha": {"tables": {"parentt": parent, "childt": child}}}
    )
    assert found == [("childt", col, "parentt", col)], found


def test_control_a_reference_style_name_on_a_weak_key_still_links(tmp_path, monkeypatch):
    found = _found(tmp_path, monkeypatch, _ordinary_pair_reference_weak())
    assert found == [("childt", COL, "parentt", COL)], found


def _ordinary_pair_reference_weak():
    return {
        "alpha": {
            "tables": {
                "parentt": {"fields": [("rowkey", "integer"), (COL, "integer")], "weak_key": COL},
                "childt": {"fields": [("rowid", "integer"), (COL, "integer")]},
            }
        }
    }


def test_control_a_zero_overlap_still_drops_a_reference_style_candidate(tmp_path, monkeypatch):
    """The zero-overlap rule (DEC-16 era, P6-2) is untouched for names that still link."""
    tables = {
        "parentt": {"fields": [("rowkey", "integer"), (COL, "text")], "unique": {COL: 1.0},
                    "values": {COL: [f"p{i}" for i in range(N + 3)]}},
        "childt": {"fields": [("rowid", "integer"), (COL, "text")],
                   "values": {COL: [f"c{i}" for i in range(N + 3)]}},
    }
    assert _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}) == []


def test_control_a_snapshot_with_only_dropped_ordinary_names_still_assembles(
    tmp_path, monkeypatch
):
    snapshot = build_snapshot(tmp_path, monkeypatch, _ordinary_pair("description"))
    assert builder_run(snapshot)["source_id"] == "builder"


# --- (b) the tie-break --------------------------------------------------------------------------


def test_control_without_p6_4_the_cut_is_by_id_which_is_the_premise_of_the_f10_finding(
    tmp_path, monkeypatch
):
    """Seven measured parents, all alike: the five smallest ids are kept (rule 3 stays)."""
    tables = {"shipment": _child_table()}
    tables.update({f"p{n}": _role_table("measured") for n in range(2, 9)})
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, top_k_per_field=5)
    lowest = sorted(range(2, 9), key=lambda n: _fid("alpha", n))[:5]
    assert _kept_parents(found) == {f"p{n}" for n in lowest}, found


def test_control_among_equal_evidence_the_smallest_parent_id_wins(tmp_path, monkeypatch):
    tables = {"shipment": _child_table(), "p2": _role_table("measured"),
              "p3": _role_table("measured")}
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, top_k_per_field=1)
    smaller = min((2, 3), key=lambda n: _fid("alpha", n))
    assert _kept_parents(found) == {f"p{smaller}"}, found


def test_a_same_source_weak_key_that_sorts_sixth_by_id_is_kept_at_top_k_five(
    tmp_path, monkeypatch
):
    """The F10 case: `ch.Company.companyID` was 6th by id for both children and was cut."""
    ranked = sorted(range(2, 9), key=lambda n: _fid("alpha", n))
    weak_table = ranked[5]
    assert len(ranked) == 7 and weak_table not in ranked[:5]  # premise: it sorts 6th
    tables = {"shipment": _child_table()}
    for n in range(2, 9):
        tables[f"p{n}"] = _role_table("weak" if n == weak_table else "measured")
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, top_k_per_field=5)
    assert _kept_parents(found) == {f"p{weak_table}"} | {f"p{n}" for n in ranked[:4]}, found


def _far_ids(count):
    return [_fid(f"far{k}", 1) for k in range(1, count + 1)]


def _near_source_sorting(rank_index, far_ids, table_number=2):
    """A source name whose parent field id sorts at `rank_index` among `far_ids` plus itself."""
    for k in range(5000):
        name = f"near{k}"
        mine = _fid(name, table_number)
        if sorted([*far_ids, mine]).index(mine) == rank_index:
            return name
    raise AssertionError("no source name found")  # pragma: no cover


def test_a_same_source_weak_key_beats_six_cross_source_declared_keys_that_sort_before_it(
    tmp_path, monkeypatch
):
    """Rule 1 (same source before cross-source) outranks rule 2 (declared before weak)."""
    far = _far_ids(6)
    near = _near_source_sorting(5, far)  # premise: the weak parent sorts 6th of seven by id
    assert sorted([*far, _fid(near, 2)]).index(_fid(near, 2)) == 5
    sources = {near: {"tables": {"shipment": _child_table(), "weakp": _role_table("weak")}}}
    for k in range(1, 7):
        sources[f"far{k}"] = {"tables": {f"farp{k}": _role_table("declared")}}
    found = _found(tmp_path, monkeypatch, sources, top_k_per_field=5)
    kept = _kept_parents(found)
    lowest_far = sorted(range(1, 7), key=lambda k: _fid(f"far{k}", 1))[:4]
    assert kept == {"weakp"} | {f"farp{k}" for k in lowest_far}, found


@pytest.mark.parametrize("same_role", ["declared", "weak", "measured"])
def test_a_same_source_parent_beats_a_cross_source_declared_key_whatever_the_id(
    tmp_path, monkeypatch, same_role
):
    far = [_fid("faraway", 1)]
    near = _near_source_sorting(1, far)  # premise: the same-source id is the larger one
    assert _fid(near, 2) > _fid("faraway", 1)
    sources = {
        near: {"tables": {"shipment": _child_table(), "nearp": _role_table(same_role)}},
        "faraway": {"tables": {"farp": _role_table("declared")}},
    }
    found = _found(tmp_path, monkeypatch, sources, top_k_per_field=1)
    assert _kept_parents(found) == {"nearp"}, found


@pytest.mark.parametrize(
    ("better", "worse"),
    [("declared", "weak"), ("declared", "measured"), ("weak", "measured")],
)
def test_evidence_orders_equal_scores_in_one_source_declared_then_weak_then_measured(
    tmp_path, monkeypatch, better, worse
):
    """The better evidence is planted on the LARGER id, so id order alone would pick the worse."""
    larger, smaller = sorted((2, 3), key=lambda n: _fid("alpha", n), reverse=True)
    assert _fid("alpha", larger) > _fid("alpha", smaller)
    roles = {larger: better, smaller: worse}
    tables = {"shipment": _child_table()}  # table numbers follow insertion order: 2, then 3
    tables.update({f"p{n}": _role_table(roles[n]) for n in (2, 3)})
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, top_k_per_field=1)
    assert _kept_parents(found) == {f"p{larger}"}, found


def test_declared_and_weak_both_survive_a_cut_of_five_among_seven_tied_parents(
    tmp_path, monkeypatch
):
    """Declared and weak planted on the two largest ids; five measured parents compete for 3."""
    ranked = sorted(range(2, 9), key=lambda n: _fid("alpha", n))
    declared_table, weak_table = ranked[6], ranked[5]
    tables = {"shipment": _child_table()}
    for n in range(2, 9):
        role = "declared" if n == declared_table else "weak" if n == weak_table else "measured"
        tables[f"p{n}"] = _role_table(role)
    found = _found(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, top_k_per_field=5)
    expected = {f"p{declared_table}", f"p{weak_table}"} | {f"p{n}" for n in ranked[:3]}
    assert _kept_parents(found) == expected, found


def test_control_a_higher_score_still_beats_the_same_source(tmp_path, monkeypatch):
    """Rule 1 applies only among EQUAL scores: a cross-source exact key name (100) outranks a
    same-source fuzzy table-name match (`widgetz`, below 100)."""
    far = [_fid("faraway", 1)]
    near = _near_source_sorting(1, far)
    sources = {
        near: {"tables": {
            "shipment": _child_table(),
            "widgetz": {"fields": [("id", "integer")], "key": ["id"]},
        }},
        "faraway": {"tables": {"farp": _role_table("declared")}},
    }
    found = _found(tmp_path, monkeypatch, sources, top_k_per_field=1)
    assert _kept_parents(found) == {"farp"}, found
