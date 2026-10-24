"""P7-2 (live finding F15): measured evidence ranks above unmeasured before `top_k_per_field` cuts.

Derived from `docs/plans/PHASE7_LOCAL_PLAN.md` section 4 "P7-2" and section 2, and from the F15
paragraph of the step 2 summary and of the F14-F17 research note, not from the implementation.

Spec as checked here. In the candidate loop of `builder.assemble`:

* `_overlap` is computed for every name-filtered candidate before sorting. The measured-zero drop
  (a `value_containment` with 0 shared tokens against a complete parent) moves with it: the same
  candidates drop.
* Sort key `(-name_score, 0 if declared unique key else 1, evidence_tier, -containment,
  other_source, claim, id)`. `evidence_tier`: measured 0 (value_containment, full measurement),
  lower bound 1 (value_containment from a parent that is not complete, or a capped one not compared
  in a common hash range), unmeasured 2 (`value_overlap_not_measured`). Containment is the
  contained fraction, or 0 when there is none. Then the `top_k_per_field` cut.

Tests marked "fails before" pin a behaviour change; `control_*` pass before and after and guard
against an over-fix (tier before name score, declared keys losing, the zero drop changing).
Fixtures use invented generic names (plan section 2.4); no value is printed.

Where a test depends on id order (the old tie-break ended in `parent.id`), it computes the ids
the same way the kit does and asserts that premise before asserting the outcome.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "catalogue_fixes"))
import catalogue_fixture_kit as kit  # noqa: E402
from catalogue_fixture_kit import build_snapshot, builder_run, candidates  # noqa: E402
from dbig_builder.builder import MIN_TOKEN_SUPPORT  # noqa: E402
from dbig_contracts.ids import stable_id  # noqa: E402

COL = "widgetid"
CHILD_TABLE = "shipment"
CHILD_VALUES = [f"c{i}" for i in range(10)]  # 10 distinct, above MIN_TOKEN_SUPPORT
FULL = list(CHILD_VALUES)  # contains every child value: containment 1.0
HALF = CHILD_VALUES[:5] + [f"x{i}" for i in range(5)]  # contains 5 of 10: containment 0.5
TINY = CHILD_VALUES[: MIN_TOKEN_SUPPORT - 2]  # below the minimum support: sample_too_small
DISJOINT = [f"z{i}" for i in range(10)]  # enough values, none shared with the child


def _parent_table(role, values, col=COL):
    """One parent table whose only interesting column is `col`; `role` is its key claim."""
    table = {"fields": [(col, "integer")], "values": {col: values}}
    if role == "declared":
        table["key"] = [col]
    elif role == "weak":
        table["weak_key"] = col
    elif role == "measured":
        table["unique"] = {col: 1.0}
    else:  # pragma: no cover - a typo in a test
        raise ValueError(role)
    return table


def _world(parents, *, child_col=COL, child_values=CHILD_VALUES):
    """`parents`: {source_id: (table name, role, values, parent column)}, all apart from `alpha`."""
    sources = {
        "alpha": {
            "tables": {
                CHILD_TABLE: {
                    "fields": [("rowid", "integer"), (child_col, "integer")],
                    "values": {child_col: child_values},
                }
            }
        }
    }
    for source_id, (table, role, values, *col) in parents.items():
        sources[source_id] = {
            "kind": "clickhouse" if role == "weak" else "postgresql",
            "tables": {table: _parent_table(role, values, *(col or [COL]))},
        }
    return sources


def _kept(tmp_path, monkeypatch, parents, *, top_k=5, name="snapshot", **kwargs):
    snapshot = build_snapshot(
        tmp_path,
        monkeypatch,
        _world(parents, **kwargs),
        name=name,
        builder_extra={"top_k_per_field": top_k},
    )
    return snapshot, {
        parent for child, _, parent, _ in candidates(snapshot) if child == CHILD_TABLE
    }


def _partial_gamma(monkeypatch):
    original = kit.token_set

    def wrapper(field_id, object_id, values, *, source_id, key_id="run-1"):
        item = original(field_id, object_id, values, source_id=source_id, key_id=key_id)
        return item.model_copy(update={"complete": False}) if source_id == "gamma" else item

    monkeypatch.setattr(kit, "token_set", wrapper)


def _field_id(source, table_number, ordinal=1):
    return stable_id(source, "field", f"{table_number}:{ordinal}")


# --- 1. the F15 shape --------------------------------------------------------------------------


@pytest.mark.parametrize(("weak_source", "measured_source"), [("beta", "gamma"), ("gamma", "beta")])
def test_f15_a_measured_unique_parent_beats_a_weak_unmeasured_key_at_top_k_one(
    tmp_path, monkeypatch, weak_source, measured_source
):
    """Fails before: the old order put the weak key claim ahead of measured uniqueness."""
    parents = {
        weak_source: ("vendora", "weak", TINY, COL),
        measured_source: ("vendorb", "measured", FULL, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept == {"vendorb"}, kept


def test_f15_control_both_survive_when_top_k_leaves_room(tmp_path, monkeypatch):
    parents = {
        "beta": ("vendora", "weak", TINY, COL),
        "gamma": ("vendorb", "measured", FULL, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=2)
    assert kept == {"vendora", "vendorb"}, kept


def test_f15_the_unmeasured_candidate_is_still_labelled_unmeasured(tmp_path, monkeypatch):
    """The weak one stays visible with its honest label; the change only reorders."""
    snapshot, kept = _kept(
        tmp_path,
        monkeypatch,
        {"beta": ("vendora", "weak", TINY, COL), "gamma": ("vendorb", "measured", FULL, COL)},
        top_k=2,
    )
    text = kit.candidate_text(snapshot)
    assert "sample_too_small" in text and "weak key claim" in text
    assert any(m["contained_fraction"] == 1.0 for m in kit.containment_metrics(snapshot))
    assert kept == {"vendora", "vendorb"}


# --- 2. a declared key still wins --------------------------------------------------------------


@pytest.mark.parametrize("rival_role", ["measured", "weak"])
@pytest.mark.parametrize(
    ("declared_source", "rival_source"), [("beta", "gamma"), ("gamma", "beta")]
)
def test_control_a_declared_key_with_unmeasured_overlap_beats_a_measured_parent(
    tmp_path, monkeypatch, rival_role, declared_source, rival_source
):
    """Passes before and after. Guards against putting the evidence tier ahead of declared keys."""
    parents = {
        declared_source: ("vendora", "declared", TINY, COL),
        rival_source: ("vendorb", rival_role, FULL, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept == {"vendora"}, kept


# --- 3. the name score still comes first -------------------------------------------------------


@pytest.mark.parametrize(("weak_source", "measured_source"), [("beta", "gamma"), ("gamma", "beta")])
def test_control_a_higher_name_score_beats_a_better_evidence_tier(
    tmp_path, monkeypatch, weak_source, measured_source
):
    """`vendor_id` meets table `vendors` (score 100) and `vendork` (score 92; both columns `id`).

    The exact-name table is a weak key with too few tokens; the near-name table is measured at
    100%. The name score outranks the tier, so the exact-name table is kept.
    """
    parents = {
        weak_source: ("vendors", "weak", TINY, "id"),
        measured_source: ("vendork", "measured", FULL, "id"),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1, child_col="vendor_id")
    assert kept == {"vendors"}, kept


# --- 4. the measured-zero drop is unchanged ----------------------------------------------------

# Recorded on the code before P7-2. Three pairs have 0 shared tokens in this fixture: the child
# against the disjoint parent, and the two parent columns against each other (each is also a child
# field of the other, and the other side is complete). Nothing is cut at top_k 5.
CURRENT_DROPPED_ZERO = 3


def _dropped(snapshot):
    return builder_run(snapshot).get("dropped_zero_overlap_candidates", 0)


def test_control_a_complete_parent_with_zero_shared_tokens_drops_and_is_counted(
    tmp_path, monkeypatch
):
    snapshot, kept = _kept(
        tmp_path,
        monkeypatch,
        {
            "beta": ("vendora", "measured", DISJOINT, COL),
            "gamma": ("vendorb", "measured", FULL, COL),
        },
    )
    assert kept == {"vendorb"}, kept
    assert _dropped(snapshot) == CURRENT_DROPPED_ZERO


def test_control_a_lone_zero_parent_leaves_no_candidate_and_one_drop(tmp_path, monkeypatch):
    snapshot, kept = _kept(tmp_path, monkeypatch, {"beta": ("vendora", "measured", DISJOINT, COL)})
    assert kept == set()
    assert _dropped(snapshot) == 1


@pytest.mark.parametrize("zero_source", ["beta", "gamma", "delta"])
def test_control_the_zero_drop_does_not_depend_on_the_cut_order(tmp_path, monkeypatch, zero_source):
    """Whichever source holds the zero parent, at top_k 1 the kept parent is the one that shares."""
    others = [s for s in ("beta", "gamma", "delta") if s != zero_source]
    parents = {
        zero_source: ("vendora", "measured", DISJOINT, COL),
        others[0]: ("vendorb", "measured", FULL, COL),
        others[1]: ("vendorc", "measured", HALF, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept == {"vendorb"} or kept == {"vendorc"}
    assert "vendora" not in kept


def test_control_a_zero_against_a_partly_read_parent_is_kept_not_dropped(tmp_path, monkeypatch):
    """The `parent_complete` condition travels with the drop: a sampled parent is no measurement."""
    _partial_gamma(monkeypatch)
    snapshot, kept = _kept(tmp_path, monkeypatch, {"gamma": ("vendora", "measured", DISJOINT, COL)})
    assert kept == {"vendora"}
    assert _dropped(snapshot) == 0


def test_f15_the_measured_zero_drop_applies_to_a_candidate_that_would_have_been_cut(
    tmp_path, monkeypatch
):
    """With the overlap known before the sort, a zero parent never takes a slot at top_k 1.

    A declared zero parent still ranks first by key claim, and a declared key is exempt from
    nothing: a measured zero on a complete parent drops it, so the sharing parent is kept.
    """
    parents = {
        "beta": ("vendora", "declared", DISJOINT, COL),
        "gamma": ("vendorb", "measured", FULL, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept == {"vendorb"}, kept


# --- 5. among measured parents, higher containment first ---------------------------------------


def test_f15_among_two_measured_parents_higher_containment_ranks_first(tmp_path, monkeypatch):
    """Fails before: with equal name, source and claim the old order was the parent id.

    The low-containment parent is given the source whose field id sorts first, so the old order
    would keep it at top_k 1.
    """
    sources = ("beta", "gamma")
    low_source = min(sources, key=lambda source: _field_id(source, 1))
    high_source = next(s for s in sources if s != low_source)
    assert _field_id(low_source, 1) < _field_id(high_source, 1)  # the premise
    parents = {
        low_source: ("vendora", "measured", HALF, COL),
        high_source: ("vendorb", "measured", FULL, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept == {"vendorb"}, kept


def test_control_among_two_measured_parents_the_better_one_wins_whatever_the_id_order(
    tmp_path, monkeypatch
):
    """The mirrored assignment: the old id order already picked the better parent."""
    sources = ("beta", "gamma")
    high_source = min(sources, key=lambda source: _field_id(source, 1))
    low_source = next(s for s in sources if s != high_source)
    parents = {
        low_source: ("vendora", "measured", HALF, COL),
        high_source: ("vendorb", "measured", FULL, COL),
    }
    _, kept = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept == {"vendorb"}, kept


# --- 6. the middle tier: a lower bound sits between measured and unmeasured --------------------


def test_f15_measured_then_lower_bound_then_unmeasured(tmp_path, monkeypatch):
    """Fails before. `gamma` is a partly read parent (a lower bound) that holds every child value,
    `beta` is measured at 50%, `delta` is an unmeasured weak key. Tier beats containment, so
    top_k 1 keeps `beta` and top_k 2 keeps `beta` and `gamma`.
    """
    _partial_gamma(monkeypatch)
    parents = {
        "beta": ("vendora", "measured", HALF, COL),
        "gamma": ("vendorb", "measured", FULL, COL),
        "delta": ("vendorc", "weak", TINY, COL),
    }
    _, kept_one = _kept(tmp_path, monkeypatch, parents, top_k=1)
    assert kept_one == {"vendora"}, kept_one
    _, kept_two = _kept(tmp_path, monkeypatch, parents, top_k=2, name="second")
    assert kept_two == {"vendora", "vendorb"}, kept_two


# --- fixture sanity ----------------------------------------------------------------------------


def test_control_the_fixture_values_have_the_sizes_the_tests_rely_on():
    assert len(set(TINY)) < MIN_TOKEN_SUPPORT
    assert len(set(FULL)) >= MIN_TOKEN_SUPPORT and len(set(HALF)) >= MIN_TOKEN_SUPPORT
    assert not set(DISJOINT) & set(CHILD_VALUES)
    assert len(set(HALF) & set(CHILD_VALUES)) * 2 == len(CHILD_VALUES)
