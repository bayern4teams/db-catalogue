"""P6-4 attack (F10, DEC-20): independent checks written by the verifier, invented names only.

Run by path (attack files are not collected by default):
    .venv/bin/python -m pytest -q tests/catalogue_fixes/attack6_f10.py
"""

from __future__ import annotations

import pytest
from catalogue_fixture_kit import build_snapshot, builder_run, candidates
from dbig_builder.builder import MIN_TOKEN_SUPPORT
from dbig_contracts.ids import stable_id

N = MIN_TOKEN_SUPPORT
OLD_KEY = "dropped_unmeasured_key_name_candidates"
NEW_KEY = "dropped_ordinary_name_candidates"


def fid(source, table, ordinal=1):
    return stable_id(source, "field", f"{table}:{ordinal}")


def vals(prefix="v", count=N + 3):
    return [f"{prefix}{i}" for i in range(count)]


def far_sources(prefix, below, count, table=1):
    """`count` source names whose field id for `table`:1 sorts below `below`."""
    names = []
    for k in range(200000):
        name = f"{prefix}{k}"
        if fid(name, table) < below:
            names.append(name)
            if len(names) == count:
                return names
    raise AssertionError("not enough sources")  # pragma: no cover


def near_source_with_large_id(table=2):
    """A near source whose parent id is large, so far ids below it are easy to find."""
    best = max((f"near{k}" for k in range(300)), key=lambda n: fid(n, table))
    return best


def of(found, child):
    """Candidates whose child table is `child`, as (parent table, parent field)."""
    return sorted((p, f) for c, _, p, f in found if c == child)


def snap(tmp_path, monkeypatch, sources, name="snapshot", **extra):
    return build_snapshot(tmp_path, monkeypatch, sources, name=name, builder_extra=extra or None)


# --- (4) the live analogue ---------------------------------------------------------------------


@pytest.mark.parametrize("sampled", [False, True])
def test_live_analogue_clickhouse_weak_key_same_source_survives_six_measured_cross_source(
    tmp_path, monkeypatch, sampled
):
    """Two tables in one ClickHouse-like source; the weak parent's id sorts after six
    measured-unique `widgetID` parents in other sources; default top_k (5)."""
    near = near_source_with_large_id(3)
    mine = fid(near, 3)
    far = far_sources("far", mine, 6)
    child_a = {"fields": [("rowid", "Int64"), ("widgetID", "Int64")]}
    child_b = {"fields": [("rowid", "Int64"), ("widgetID", "Int64")]}
    parent = {"fields": [("widgetID", "Int64")], "weak_key": "widgetID"}
    if sampled:
        for table in (child_a, child_b):
            table["values"] = {"widgetID": vals()}
        parent["values"] = {"widgetID": vals()}
    sources = {
        near: {
            "kind": "clickhouse",
            "tables": {"experience": child_a, "widgetindustry": child_b, "company": parent},
        }
    }
    # the parents in other sources: measured unique; their ids sort before the weak one
    for name in far:
        table = {"fields": [("widgetID", "Int64")], "unique": {"widgetID": 1.0}}
        if sampled:
            table["values"] = {"widgetID": vals()}
        sources[name] = {"tables": {"farp": table}}
    # recompute: the near parent is table 3 in this layout; check the premise on the real id
    mine = fid(near, 3)
    assert sum(fid(name, 1) < mine for name in far) == 6, "premise: six cross-source ids sort first"
    found = candidates(snap(tmp_path, monkeypatch, sources))
    for child in ("experience", "widgetindustry"):
        parents = of(found, child)
        assert ("company", "widgetID") in parents, (child, found)
        assert len(parents) == 5, (child, parents)


def test_live_analogue_old_code_would_cut_it_premise_is_real(tmp_path, monkeypatch):
    """Sanity of the fixture: ordering by (-score, id) alone puts the weak parent 7th of 7."""
    near = near_source_with_large_id(3)
    mine = fid(near, 3)
    far = far_sources("far", mine, 6)
    ids = sorted([fid(n, 1) for n in far] + [mine])
    assert ids.index(mine) == 6


# --- (1) the count ------------------------------------------------------------------------------


def _ordinary_fanout(parents, children, sampled=False, cross=False):
    tables = {}
    for k in range(children):
        t = {"fields": [("rowid", "integer"), ("description", "text")]}
        if sampled:
            t["values"] = {"description": vals()}
        tables[f"child{k}"] = t
    sources = {"alpha": {"tables": tables}}
    for k in range(parents):
        t = {"fields": [("rowkey", "integer"), ("description", "text")],
             "unique": {"description": 1.0}}
        if sampled:
            t["values"] = {"description": vals()}
        if cross:
            sources[f"beta{k}"] = {"tables": {"parentt": t}}
        else:
            sources["alpha"]["tables"][f"parent{k}"] = t
    return sources


@pytest.mark.parametrize("top_k", [1, 5, 100])
def test_count_is_per_child_parent_pair_and_independent_of_top_k(tmp_path, monkeypatch, top_k):
    snapshot = snap(tmp_path, monkeypatch, _ordinary_fanout(3, 2), top_k_per_field=top_k)
    assert candidates(snapshot) == []
    run = builder_run(snapshot)
    assert run[NEW_KEY] == 12, run
    assert OLD_KEY not in run


def test_count_pairs_of_children_with_each_other_are_counted_too_exactly_once(
    tmp_path, monkeypatch
):
    """3 undeclared parents + 2 children, all named `description`, all unique: every ordered
    pair of different tables is a (child, parent) pair: 5 children x 4 other parents = 20 is
    wrong if only parents count. Parents are exactly the 3 unique-measured; children are all 5."""
    run = builder_run(snap(tmp_path, monkeypatch, _ordinary_fanout(3, 2)))
    # children (5 fields) x parents (3 measured) minus same-table self pairs (3) = 12
    assert run[NEW_KEY] == 12


def test_count_absent_for_declared_only_and_reference_names(tmp_path, monkeypatch):
    t = {
        "declt": {"fields": [("rowkey", "integer"), ("description", "text")],
                  "key": ["description"]},
        "parentt": {"fields": [("rowkey", "integer"), ("widgetid", "integer")],
                    "unique": {"widgetid": 1.0}},
        "childt": {"fields": [("rowid", "integer"), ("description", "text"),
                              ("widgetid", "integer")]},
    }
    snapshot = snap(tmp_path, monkeypatch, {"alpha": {"tables": t}})
    run = builder_run(snapshot)
    assert NEW_KEY not in run and OLD_KEY not in run
    assert sorted(of(candidates(snapshot), "childt")) == [
        ("declt", "description"), ("parentt", "widgetid")]


def test_count_is_int_only_no_value_or_name_in_the_manifest(tmp_path, monkeypatch):
    run = builder_run(snap(tmp_path, monkeypatch, _ordinary_fanout(2, 1, sampled=True)))
    assert isinstance(run[NEW_KEY], int)
    blob = repr(run)
    assert "v0" not in blob and "description" not in blob.replace(NEW_KEY, "")


def test_snapshot_without_ordinary_drop_has_no_new_manifest_key(tmp_path, monkeypatch):
    t = {"parentt": {"fields": [("widgetid", "integer")], "key": ["widgetid"]},
         "childt": {"fields": [("rowid", "integer"), ("widgetid", "integer")]}}
    run = builder_run(snap(tmp_path, monkeypatch, {"alpha": {"tables": t}}))
    assert NEW_KEY not in run and OLD_KEY not in run


# --- (1) other rules still letting an ordinary name in ------------------------------------------


@pytest.mark.parametrize("child_name", ["Description", "DESCRIPTION"])
def test_case_variants_of_an_ordinary_name_are_dropped(tmp_path, monkeypatch, child_name):
    t = {
        "parentt": {"fields": [("rowkey", "integer"), ("description", "text")],
                    "unique": {"description": 1.0}, "values": {"description": vals()}},
        "childt": {"fields": [("rowid", "integer"), (child_name, "text")],
                   "values": {child_name: vals()}},
    }
    assert candidates(snap(tmp_path, monkeypatch, {"alpha": {"tables": t}})) == []


def test_parent_table_named_like_the_ordinary_column_does_not_use_exact_name(
    tmp_path, monkeypatch
):
    """`description.description` (measured) and child `description`: previously the colleague
    flagged a fall-through to exact_name; the `continue` closes it."""
    t = {
        "description": {"fields": [("description", "text")], "unique": {"description": 1.0},
                        "values": {"description": vals()}},
        "childt": {"fields": [("rowid", "integer"), ("description", "text")],
                   "values": {"description": vals()}},
    }
    assert candidates(snap(tmp_path, monkeypatch, {"alpha": {"tables": t}})) == []


def test_a_weak_key_claim_on_an_ordinary_name_across_sources_with_overlap_is_dropped(
    tmp_path, monkeypatch
):
    sources = {
        "ch": {"kind": "clickhouse", "tables": {"mails": {
            "fields": [("freeMails", "String")], "weak_key": "freeMails",
            "values": {"freeMails": vals()}}}},
        "pg": {"tables": {"kids": {"fields": [("rowid", "integer"), ("freeMails", "String")],
                                    "values": {"freeMails": vals()}}}},
    }
    assert candidates(snap(tmp_path, monkeypatch, sources)) == []


def test_declared_key_ordinary_name_beside_many_undeclared_ordinary_parents(
    tmp_path, monkeypatch
):
    tables = {"kid": {"fields": [("rowid", "integer"), ("email", "text")],
                      "values": {"email": vals()}},
              "decl": {"fields": [("email", "text")], "key": ["email"],
                       "values": {"email": vals()}}}
    for k in range(8):
        tables[f"m{k}"] = {"fields": [("email", "text")], "unique": {"email": 1.0},
                           "values": {"email": vals()}}
    found = candidates(snap(tmp_path, monkeypatch, {"alpha": {"tables": tables}}))
    assert of(found, "kid") == [("decl", "email")], found


def test_composite_declared_key_with_ordinary_component_names_is_untouched(
    tmp_path, monkeypatch
):
    """DEC-3 composite candidates only exist against declared keys; DEC-20 must not touch them."""
    tables = {
        "parentt": {"fields": [("description", "text"), ("email", "text")],
                    "key": ["description", "email"]},
        "childt": {"fields": [("rowid", "integer"), ("parentt_description", "text"),
                              ("parentt_email", "text")]},
    }
    # Only that it assembles and a declared key is never counted as a dropped ordinary name.
    run = builder_run(snap(tmp_path, monkeypatch, {"alpha": {"tables": tables}}))
    assert NEW_KEY not in run


# --- (3) tie-break details ----------------------------------------------------------------------


def test_zero_overlap_drops_do_not_consume_slots_across_the_new_ordering(tmp_path, monkeypatch):
    """Six same-source parents (ranked first) with a complete, disjoint value set are dropped;
    the single cross-source parent with real overlap must still be kept at top_k 5."""
    tables = {"kid": {"fields": [("rowid", "integer"), ("widgetid", "text")],
                      "values": {"widgetid": vals("c")}}}
    for k in range(6):
        tables[f"same{k}"] = {"fields": [("widgetid", "text")], "unique": {"widgetid": 1.0},
                              "values": {"widgetid": vals(f"z{k}_")}}
    sources = {"alpha": {"tables": tables},
               "beta": {"tables": {"farp": {"fields": [("widgetid", "text")],
                                            "key": ["widgetid"],
                                            "values": {"widgetid": vals("c")}}}}}
    found = candidates(snap(tmp_path, monkeypatch, sources, top_k_per_field=5))
    assert of(found, "kid") == [("farp", "widgetid")], found


def test_source_groups_filter_before_ranking_same_source_still_first(tmp_path, monkeypatch):
    """DEC-3 groups: a forbidden/ungrouped source never reaches the ranking; among the allowed,
    same source stays first even when its id is the largest."""
    near = near_source_with_large_id()
    tables = {"kid": {"fields": [("rowid", "integer"), ("widgetid", "integer")]},
              "nearp": {"fields": [("widgetid", "integer")], "unique": {"widgetid": 1.0}}}
    mine = fid(near, 2)
    far = far_sources("grp", mine, 3)
    sources = {near: {"tables": tables}}
    for name in far:
        sources[name] = {"tables": {"farp": {"fields": [("widgetid", "integer")],
                                            "key": ["widgetid"]}}}
    forbidden = (near, far[0])
    found = candidates(snap(tmp_path, monkeypatch, sources, top_k_per_field=1,
                            forbidden_source_pairs=[forbidden]))
    assert of(found, "kid") == [("nearp", "widgetid")], found
    found_groups = candidates(snap(tmp_path, monkeypatch, sources, name="g", top_k_per_field=5,
                                   source_groups=[[near, far[1]]]))
    # far[0], far[2] sit in no group, so pair_allowed is true for them; all four parents allowed
    assert len(of(found_groups, "kid")) == 4, found_groups


def test_equal_evidence_across_runs_is_deterministic(tmp_path, monkeypatch):
    tables = {"kid": {"fields": [("rowid", "integer"), ("widgetid", "integer")]}}
    for k in range(8):
        tables[f"p{k}"] = {"fields": [("widgetid", "integer")], "unique": {"widgetid": 1.0}}
    a = candidates(snap(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name="a"))
    b = candidates(snap(tmp_path, monkeypatch, {"alpha": {"tables": tables}}, name="b"))
    assert a == b and len(of(a, "kid")) == 5


def test_child_in_cross_source_only_uses_declared_then_weak_then_measured(tmp_path, monkeypatch):
    """All parents cross-source: rule 2 decides, larger id carries the better evidence."""
    ids = {}
    roles = ["declared", "weak", "measured"]
    names = [f"s{k}" for k in range(3)]
    order = sorted(names, key=lambda n: fid(n, 1))
    role_of = dict(zip(order[::-1], roles, strict=True))  # best evidence on the LARGEST id
    sources = {"kidsrc": {"tables": {"kid": {"fields": [("rowid", "integer"),
                                                         ("widgetid", "integer")]}}}}
    for name in names:
        role = role_of[name]
        t = {"fields": [("widgetid", "integer")]}
        if role == "declared":
            t["key"] = ["widgetid"]
        elif role == "weak":
            t["weak_key"] = "widgetid"
        else:
            t["unique"] = {"widgetid": 1.0}
        sources[name] = {"tables": {f"p_{name}": t}}
    found = candidates(snap(tmp_path, monkeypatch, sources, top_k_per_field=2))
    best_two = {f"p_{n}" for n, r in role_of.items() if r in ("declared", "weak")}
    assert {p for p, _ in of(found, "kid")} == best_two, (found, role_of)
    del ids


def test_a_weak_key_that_is_also_checked_for_a_table_with_a_declared_key_is_not_a_parent(
    tmp_path, monkeypatch
):
    """Existing rule untouched: a table that declares a key does not also offer a weak key."""
    t = {"kid": {"fields": [("rowid", "integer"), ("widgetid", "integer")]},
         "p": {"fields": [("rowkey", "integer"), ("widgetid", "integer")], "key": ["rowkey"],
               "weak_key": "widgetid"}}
    assert candidates(snap(tmp_path, monkeypatch, {"alpha": {"tables": t}})) == []
